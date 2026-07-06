from __future__ import annotations

import json
from pathlib import Path

import udi_interface

# Notice how clean the imports are now!
from isy2ntfy_node import ISY2Ntfy, Settings

LOGGER = udi_interface.LOGGER
polyglot = None
DEFAULT_NTFY_URL = "https://ntfy.sh"
DEFAULT_MESSAGE_TAG = "bell"
DEFAULT_STARTUP_TAG = "rocket"


def _load_nodeserver_version() -> str:
    """Load node server version from server.json for polyglot.start(version)."""
    default_version = "0.0.1"
    server_json = Path(__file__).parent / "server.json"
    try:
        data = json.loads(server_json.read_text(encoding="utf-8"))
    except Exception:
        return default_version

    version = str(data.get("version") or "").strip()
    if version:
        return version

    credits = data.get("credits")
    if isinstance(credits, list) and credits:
        first = credits[0]
        if isinstance(first, dict):
            credits_version = str(first.get("version") or "").strip()
            if credits_version:
                return credits_version

    return default_version


class Controller(udi_interface.Node):
    id = "ntfy"
    drivers = [
        {"driver": "ST", "value": 0, "uom": 2},
    ]

    def __init__(self, poly):
        super().__init__(poly, "ntfy", "ntfy", "NTFY")

        self.poly = poly
        self.custom_params = udi_interface.Custom(poly, "customparams")
        self.bridge = None
        self._startup_announcement_sent = False
        self._customparams_bootstrapped = False
        self._counter_file = Path(__file__).parent / ".message_id_counter"
        self._message_counter = self._load_message_counter()

        self.poly.subscribe(self.poly.CUSTOMPARAMS, self.parameter_handler)
        try:
            self.poly.subscribe(self.poly.START, self.start, address=self.address)
        except TypeError:
            self.poly.subscribe(self.poly.START, self.start)
        self.poly.subscribe(self.poly.POLL, self.poll)

        self.commands = {
            "QUERY": self.query,
            "GV10": self.cmd_gv10,
        }

    def start(self, *_args):
        LOGGER.info("Starting NTFY")
        self._ensure_required_params()
        self._build_bridge()
        
        key = str(self.custom_params.get("KEY", "")).strip()
        if key:
            self._send_startup_announcement(source="startup")

    def poll(self, poll_type):
        if poll_type == "longPoll":
            self.setDriver("ST", 1 if self.bridge else 0, force=True)

    def parameter_handler(self, params):
        previous_key = str(self.custom_params.get("KEY", "")).strip()
        self.custom_params.load(params)
        self._ensure_required_params()
        current_key = str(self.custom_params.get("KEY", "")).strip()
        self._build_bridge()
    
        LOGGER.info("Pushing profile to IoX hub...")
        self.poly.updateProfile()

        if not self._customparams_bootstrapped:
            self._customparams_bootstrapped = True
            return

        if current_key and current_key != previous_key:
            self._send_startup_announcement(source="key-updated")

    def _ensure_required_params(self):
        required = {
            "KEY": "",
            "NTFY_URL": DEFAULT_NTFY_URL,
            "SEND_ID": "true",
            "ID_PREFIX": "msg",
        }
        changed = False
        for key, default in required.items():
            if key not in self.custom_params:
                self.custom_params[key] = default
                changed = True
        if changed:
            save_fn = getattr(self.custom_params, "save", None)
            if callable(save_fn):
                save_fn()
            LOGGER.info("Saved defaults.")

    def _send_id_enabled(self) -> bool:
        raw = str(self.custom_params.get("SEND_ID", "true")).strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def _load_message_counter(self) -> int:
        try:
            value = int(self._counter_file.read_text(encoding="utf-8").strip())
            return max(value, 0)
        except Exception:
            return 0

    def _save_message_counter(self) -> None:
        try:
            self._counter_file.write_text(str(self._message_counter), encoding="utf-8")
        except Exception as exc:
            LOGGER.warning("Unable to persist message counter: %s", exc)

    def _next_message_id(self) -> str:
        prefix = str(self.custom_params.get("ID_PREFIX", "msg")).strip() or "msg"
        self._message_counter += 1
        self._save_message_counter()
        return f"{prefix}{self._message_counter:03d}"

    def _build_message_id(self, source_id=None) -> str | None:
        if not self._send_id_enabled():
            return None
        if source_id is not None:
            sid = str(source_id).strip()
            if sid:
                return sid
        return self._next_message_id()

    def _build_bridge(self):
        key = str(self.custom_params.get("KEY", "")).strip()
        ntfy_url = str(self.custom_params.get("NTFY_URL", DEFAULT_NTFY_URL)).strip() or DEFAULT_NTFY_URL

        if not key:
            self.bridge = None
            self.setDriver("ST", 0, force=True)
            LOGGER.warning("Set KEY custom param to enable ntfy publishing")
            return

        # Initialize the clean Settings object
        settings = Settings(
            ntfy_publish_url=ntfy_url,
            ntfy_key=key,
        )
        self.bridge = ISY2Ntfy(settings)
        self.setDriver("ST", 1, force=True)

    @staticmethod
    def _extract_command(args):
        if not args:
            return None
        if len(args) == 1:
            return args[0]
        return args[-1]

    def query(self, *args):
        self.reportDrivers()

    def cmd_gv10(self, *args):
        """
        Catches the UOM 147 payload from the ISY and logs it.
        """
        command = self._extract_command(args)
        LOGGER.info("RAW NOTIFICATION COMMAND RECEIVED: %s", command)

    def _send_startup_announcement(self, source: str = "startup"):
        if source == "startup" and self._startup_announcement_sent:
            return
        if not self.bridge:
            return

        version = _load_nodeserver_version()
        title = "NTFY Started"
        body = f"NTFY node server started. Version: {version}. source={source}"
        if source == "key-updated":
            title = "NTFY Started (Key Updated)"

        try:
            # Using the new send_notification function
            response = self.bridge.send_notification(
                title=title,
                body=body,
                message_id=self._build_message_id(),
                tags=DEFAULT_STARTUP_TAG,
            )
            if source == "startup":
                self._startup_announcement_sent = True
            LOGGER.info("Sent startup announcement to ntfy. HTTP %s", response.status_code)
        except Exception as exc:
            LOGGER.warning("Unable to send startup announcement: %s", exc)


if __name__ == "__main__":
    ns_version = _load_nodeserver_version()
    LOGGER.info("Starting with node server version %s", ns_version)
    polyglot = udi_interface.Interface([])
    polyglot.start(ns_version)
    controller = Controller(polyglot)
    polyglot.addNode(controller)
    polyglot.ready()
    polyglot.runForever()