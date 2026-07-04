from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
import xml.etree.ElementTree as ET

import requests


@dataclass
class Settings:
    isy_base_url: str
    isy_username: Optional[str]
    isy_password: Optional[str]
    ntfy_topic: str
    ntfy_key: str


@dataclass
class MessageTemplate:
    template_id: int
    name: str
    body: str


class ISY2Ntfy:
    """Small integration layer intended for a PG3 node implementation.

    What this gives you now:
    - single configurable field for ntfy KEY (`ntfy_key`)
    - fetches ISY email/notification customizations
    - sends a selected customization template to ntfy
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.session = requests.Session()
        if settings.isy_username and settings.isy_password:
            self.session.auth = (settings.isy_username, settings.isy_password)
        self.session.verify = False

    @classmethod
    def from_config_file(cls, config_path: str | Path) -> "ISY2Ntfy":
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        settings = Settings(
            isy_base_url=config["isy_base_url"].rstrip("/"),
            isy_username=config.get("isy_username"),
            isy_password=config.get("isy_password"),
            ntfy_topic=config["ntfy_topic"],
            ntfy_key=config["ntfy_key"],
        )
        return cls(settings)

    def fetch_customization_messages(self) -> List[MessageTemplate]:
        """Returns ISY customization messages from the first compatible endpoint.

        Different ISY firmware builds can expose this in slightly different paths.
        We try several known endpoints and parse XML or JSON.
        """
        endpoints = [
            "/rest/notifications",
            "/rest/notifications/customizations",
        ]

        last_error: Optional[Exception] = None
        for endpoint in endpoints:
            try:
                url = f"{self.settings.isy_base_url}{endpoint}"
                response = self.session.get(url, timeout=10)
                response.raise_for_status()
                templates = self._parse_templates(response)
                if templates:
                    return templates
            except Exception as exc:  # pragma: no cover - best-effort endpoint probing
                last_error = exc

        if last_error:
            raise RuntimeError(
                "Unable to fetch ISY notification customizations from known endpoints"
            ) from last_error
        return []

    def send_customization_to_ntfy(self, template_id: int) -> requests.Response:
        templates = self.fetch_customization_messages()
        selected = next((t for t in templates if t.template_id == template_id), None)
        if selected is None:
            raise ValueError(f"Template ID {template_id} not found in ISY customizations")

        return self._publish_to_ntfy(selected)

    def build_template_options(self) -> Dict[int, str]:
        """Returns template choices for a PG3 editor dropdown/selector mapping."""
        options: Dict[int, str] = {}
        for template in self.fetch_customization_messages():
            label = f"{template.template_id}: {template.name}".strip()
            options[template.template_id] = label
        return options

    def _publish_to_ntfy(self, template: MessageTemplate) -> requests.Response:
        url = f"https://ntfy.sh/{self.settings.ntfy_topic}"
        headers = {
            "Authorization": f"Bearer {self.settings.ntfy_key}",
            "Title": template.name or "ISY Notification",
            "Tags": "bell",
        }
        response = requests.post(url, data=template.body.encode("utf-8"), headers=headers, timeout=10)
        response.raise_for_status()
        return response

    @staticmethod
    def _parse_templates(response: requests.Response) -> List[MessageTemplate]:
        content_type = response.headers.get("content-type", "").lower()
        if "json" in content_type:
            return ISY2Ntfy._parse_templates_from_json(response.json())
        return ISY2Ntfy._parse_templates_from_xml(response.text)

    @staticmethod
    def _parse_templates_from_json(payload: object) -> List[MessageTemplate]:
        templates: List[MessageTemplate] = []

        def build(item: dict) -> Optional[MessageTemplate]:
            if not isinstance(item, dict):
                return None
            raw_id = item.get("id") or item.get("template_id") or item.get("key")
            if raw_id is None:
                return None
            try:
                template_id = int(str(raw_id).strip())
            except ValueError:
                return None
            name = str(item.get("name") or item.get("title") or f"Template {template_id}").strip()
            body = str(item.get("message") or item.get("body") or "").strip()
            return MessageTemplate(template_id=template_id, name=name, body=body)

        if isinstance(payload, list):
            for item in payload:
                template = build(item)
                if template:
                    templates.append(template)
        elif isinstance(payload, dict):
            candidate_lists = [payload.get("customizations"), payload.get("messages"), payload.get("items")]
            for candidate in candidate_lists:
                if isinstance(candidate, list):
                    for item in candidate:
                        template = build(item)
                        if template:
                            templates.append(template)

        return templates

    @staticmethod
    def _parse_templates_from_xml(xml_text: str) -> List[MessageTemplate]:
        templates: List[MessageTemplate] = []
        root = ET.fromstring(xml_text)

        # Common naming variants seen in notification XML payloads.
        for node in root.findall(".//customization") + root.findall(".//message"):
            template_id = ISY2Ntfy._find_first_text(node, ["id", "key", "num"])
            if not template_id:
                continue
            try:
                template_id_int = int(template_id)
            except ValueError:
                continue

            name = ISY2Ntfy._find_first_text(node, ["name", "title", "subject"]) or f"Template {template_id_int}"
            body = ISY2Ntfy._find_first_text(node, ["body", "message", "text"]) or ""
            templates.append(MessageTemplate(template_id=template_id_int, name=name.strip(), body=body.strip()))

        return templates

    @staticmethod
    def _find_first_text(node: ET.Element, names: List[str]) -> Optional[str]:
        for name in names:
            child = node.find(name)
            if child is not None and child.text:
                return child.text.strip()
        return None


def _demo() -> None:
    """Simple local test flow before wiring into PG3 command handlers."""
    bridge = ISY2Ntfy.from_config_file("config.example.json")

    templates = bridge.fetch_customization_messages()
    if not templates:
        print("No ISY customizations found")
        return

    print("Available customization messages:")
    for template in templates:
        print(f"  {template.template_id}: {template.name}")

    selected_id = templates[0].template_id
    response = bridge.send_customization_to_ntfy(selected_id)
    print(f"Published template {selected_id} to ntfy: {response.status_code}")


if __name__ == "__main__":
    _demo()
