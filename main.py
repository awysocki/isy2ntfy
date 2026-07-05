from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, List

import udi_interface

from isy2ntfy_node import ISY2Ntfy, MessageTemplate, Settings

LOGGER = udi_interface.LOGGER
polyglot = None
HAS_CONTROLLER_CLASS = hasattr(udi_interface, "Controller")
DEFAULT_NTFY_URL = "https://ntfy.sh"
DEFAULT_MESSAGE_TAG = "bell"
DEFAULT_STARTUP_TAG = "rocket"


def _profile_dir() -> Path:
    return Path(__file__).parent / "profile"


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


def _safe_label(text: str, limit: int = 50) -> str:
    return " ".join(text.split())[:limit] if text else "Message"


def write_msgsel_editor(templates: List[MessageTemplate]) -> None:
    editor_options = "\n".join(
        f'      <option id="{t.template_id}">{_safe_label(t.name)}</option>' for t in templates
    )
    if not editor_options:
        editor_options = '      <option id="1">Message 1</option>'

    xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
        "<editors>\n"
        "  <editor id=\"CTRLST\">\n"
        "    <range>\n"
        "      <option id=\"0\">Disconnected</option>\n"
        "      <option id=\"1\">Connected</option>\n"
        "    </range>\n"
        "  </editor>\n"
        "  <editor id=\"MSGSEL\">\n"
        "    <range>\n"
        f"{editor_options}\n"
        "    </range>\n"
        "  </editor>\n"
        "</editors>\n"
    )

    editor_file = _profile_dir() / "editor" / "editors.xml"
    editor_file.parent.mkdir(parents=True, exist_ok=True)
    editor_file.write_text(xml, encoding="utf-8")


ControllerBase = udi_interface.Controller if HAS_CONTROLLER_CLASS else udi_interface.Node


class Controller(ControllerBase):
    id = "isy2ntfy_controller"
    drivers = [
        {"driver": "ST", "value": 0, "uom": 2},
        {"driver": "GV0", "value": 1, "uom": 25},
    ]
    commands = {}

    def __init__(self, poly):
        if HAS_CONTROLLER_CLASS:
            super().__init__(poly)
        else:
            super().__init__(poly, "controller", "controller", "NTFY")

        self.poly = poly
        self.custom_params = udi_interface.Custom(poly, "customparams")
        self.bridge = None
        self.templates: Dict[int, str] = {}
        self._startup_announcement_sent = False
        self._customparams_bootstrapped = False
        self._counter_file = Path(__file__).parent / ".message_id_counter"
        self._message_counter = self._load_message_counter()

        self.poly.subscribe(self.poly.CUSTOMPARAMS, self.parameter_handler)
        try:
            self.poly.subscribe(self.poly.START, self.start, address=self.address)
        except TypeError:
            # Older interface variants do not support address kwarg on subscribe.
            self.poly.subscribe(self.poly.START, self.start)
        self.poly.subscribe(self.poly.POLL, self.poll)

        self.commands = {
            # Node.runCmd in older udi_interface calls handlers as fun(self, command).
            # Use unbound functions in this map for compatibility.
            "SEND": Controller.cmd_send,
            "REFRESH": Controller.cmd_refresh,
            "QUERY": Controller.query,
            "GV10": Controller.cmd_gv10,
        }

    def start(self, *_args):
        LOGGER.info("Starting NTFY")
        self._ensure_required_params()
        self._connect_and_refresh_templates(install_profile=True)
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
        self._connect_and_refresh_templates(install_profile=True)

        # First CUSTOMPARAMS callback is PG3 bootstrap data, not a user edit.
        if not self._customparams_bootstrapped:
            self._customparams_bootstrapped = True
            return

        # After bootstrap, notify only when KEY is actually set/changed.
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
            LOGGER.info("Saved defaults. Set KEY in PG3 Custom Configuration (NTFY_URL defaults to https://ntfy.sh).")

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

        # Optional ISY URL for template-fetch mode (SEND by template id).
        isy_url = str(self.custom_params.get("ISY_REST_URL", "")).strip() or None

        if not key:
            self.bridge = None
            self.setDriver("ST", 0, force=True)
            LOGGER.warning("Set KEY custom param to enable ntfy publishing")
            return

        settings = Settings(
            isy_base_url=isy_url.rstrip("/") if isy_url else None,
            isy_username=os.getenv("ISY_USERNAME") or None,
            isy_password=os.getenv("ISY_PASSWORD") or None,
            ntfy_publish_url=ntfy_url,
            ntfy_key=key,
        )
        self.bridge = ISY2Ntfy(settings)
        self.setDriver("ST", 1, force=True)

    def _connect_and_refresh_templates(self, install_profile: bool = False):
        self._build_bridge()
        if not self.bridge:
            return

        if not self.bridge.settings.isy_base_url:
            LOGGER.info("ISY_REST_URL not set; template dropdown refresh skipped (GV10/direct publish still available)")
            return

        try:
            templates = self.bridge.fetch_customization_messages()
            self.templates = {t.template_id: t.name for t in templates}
            write_msgsel_editor(templates)
            if templates:
                first_id = templates[0].template_id
                self.setDriver("GV0", first_id, force=True)
            if install_profile:
                self.poly.installprofile()
            LOGGER.info("Loaded %s customization messages", len(templates))
        except Exception as exc:
            self.setDriver("ST", 0, force=True)
            LOGGER.error("Unable to refresh ISY customization messages: %s", exc)

    @staticmethod
    def _extract_command(args):
        if not args:
            return None
        if len(args) == 1:
            return args[0]
        return args[-1]

    @staticmethod
    def _response_url(response) -> str:
        return str(getattr(response, "url", ""))

    def query(self, *args):
        self.reportDrivers()

    def cmd_refresh(self, *args):
        self._connect_and_refresh_templates(install_profile=True)

    def cmd_send(self, *args):
        command = self._extract_command(args)
        if not self.bridge:
            self._build_bridge()
        if not self.bridge:
            return

        # Preferred source is driver value selected from MSGSEL editor.
        selected = int(float(self.getDriver("GV0") or 1))

        # Allow command payload override if PG3 sends a value.
        if isinstance(command, dict):
            val = command.get("value")
            if val is not None:
                try:
                    selected = int(float(val))
                except Exception:
                    pass

        try:
            response = self.bridge.send_customization_to_ntfy(
                selected,
                message_id=self._build_message_id(),
                tags=DEFAULT_MESSAGE_TAG,
            )
            LOGGER.info(
                "Sent template %s to ntfy topic. HTTP %s url=%s",
                selected,
                response.status_code,
                self._response_url(response),
            )
        except Exception as exc:
            LOGGER.error("Failed to publish template %s: %s", selected, exc)

    def cmd_gv10(self, *args):
        command = self._extract_command(args)
        """Accept external notification payloads and publish directly to ntfy.

        Expected source payload shape (example):
        command["query"]["Content.uom147"]["notification"]["formatted"]
        with keys: subject/body.
        """
        if not self.bridge:
            self._build_bridge()
        if not self.bridge:
            return

        title = "ISY Notification"
        body = ""
        source_id = None

        if isinstance(command, dict):
            query = command.get("query")
            if isinstance(query, dict):
                content = query.get("Content.uom147")
                if isinstance(content, dict):
                    notification = content.get("notification")
                    if isinstance(notification, dict):
                        source_id = notification.get("@_id")
                        formatted = notification.get("formatted")
                        if isinstance(formatted, dict):
                            title = str(formatted.get("subject") or title).strip() or title
                            body = str(formatted.get("body") or "").strip()

        if not body:
            LOGGER.warning("GV10 received but no notification body found in payload")
            return

        try:
            response = self.bridge._publish_to_ntfy(
                MessageTemplate(template_id=0, name=title, body=body),
                message_id=self._build_message_id(source_id),
                tags=DEFAULT_MESSAGE_TAG,
            )
            LOGGER.info("Sent GV10 notification to ntfy. HTTP %s url=%s", response.status_code, self._response_url(response))
        except Exception as exc:
            LOGGER.error("Failed to publish GV10 notification: %s", exc)

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
            response = self.bridge._publish_to_ntfy(
                MessageTemplate(
                    template_id=0,
                    name=title,
                    body=body,
                ),
                message_id=self._build_message_id(),
                tags=DEFAULT_STARTUP_TAG,
            )
            if source == "startup":
                self._startup_announcement_sent = True
            LOGGER.info("Sent startup announcement to ntfy. HTTP %s url=%s", response.status_code, self._response_url(response))
        except Exception as exc:
            LOGGER.warning("Unable to send startup announcement: %s", exc)


if __name__ == "__main__":
    ns_version = _load_nodeserver_version()
    LOGGER.info("Starting with node server version %s", ns_version)
    polyglot = udi_interface.Interface([])
    polyglot.start(ns_version)
    controller = Controller(polyglot)
    if not HAS_CONTROLLER_CLASS:
        polyglot.addNode(controller)
    polyglot.ready()
    polyglot.runForever()
