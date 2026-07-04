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
            super().__init__(poly, "controller", "controller", "ISY2NTFY")

        self.poly = poly
        self.custom_params = udi_interface.Custom(poly, "customparams")
        self.bridge = None
        self.templates: Dict[int, str] = {}

        self.poly.subscribe(self.poly.CUSTOMPARAMS, self.parameter_handler)
        try:
            self.poly.subscribe(self.poly.START, self.start, address=self.address)
        except TypeError:
            # Older interface variants do not support address kwarg on subscribe.
            self.poly.subscribe(self.poly.START, self.start)
        self.poly.subscribe(self.poly.POLL, self.poll)

        self.commands = {
            "SEND": self.cmd_send,
            "REFRESH": self.cmd_refresh,
            "QUERY": self.query,
        }

    def start(self):
        LOGGER.info("Starting ISY2NTFY")
        self._ensure_required_params()
        self._connect_and_refresh_templates(install_profile=True)

    def poll(self, poll_type):
        if poll_type == "longPoll":
            self.setDriver("ST", 1 if self.bridge else 0, force=True)

    def parameter_handler(self, params):
        self.custom_params.load(params)
        self._ensure_required_params()
        self._connect_and_refresh_templates(install_profile=True)

    def _ensure_required_params(self):
        required = {
            "KEY": "",
            "TOPIC": "",
            "ISY_URL": "https://127.0.0.1",
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
            LOGGER.info("Saved default custom parameters. Fill KEY and TOPIC in PG3 Custom Configuration.")

    def _build_bridge(self):
        key = str(self.custom_params.get("KEY", "")).strip()
        topic = str(self.custom_params.get("TOPIC", "")).strip()
        isy_url = str(self.custom_params.get("ISY_URL", "https://127.0.0.1")).strip() or "https://127.0.0.1"

        if not key or not topic:
            self.bridge = None
            self.setDriver("ST", 0, force=True)
            LOGGER.warning("Set KEY and TOPIC custom params to enable ntfy publishing")
            return

        settings = Settings(
            isy_base_url=isy_url.rstrip("/"),
            isy_username=os.getenv("ISY_USERNAME") or None,
            isy_password=os.getenv("ISY_PASSWORD") or None,
            ntfy_topic=topic,
            ntfy_key=key,
        )
        self.bridge = ISY2Ntfy(settings)
        self.setDriver("ST", 1, force=True)

    def _connect_and_refresh_templates(self, install_profile: bool = False):
        self._build_bridge()
        if not self.bridge:
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

    def query(self, _command=None):
        self.reportDrivers()

    def cmd_refresh(self, _command=None):
        self._connect_and_refresh_templates(install_profile=True)

    def cmd_send(self, command=None):
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
            response = self.bridge.send_customization_to_ntfy(selected)
            LOGGER.info("Sent template %s to ntfy topic. HTTP %s", selected, response.status_code)
        except Exception as exc:
            LOGGER.error("Failed to publish template %s: %s", selected, exc)


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
