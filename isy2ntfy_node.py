from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlparse, urlunparse

import requests

try:
    import udi_interface  # type: ignore
    LOGGER = udi_interface.LOGGER
except Exception:  # pragma: no cover
    LOGGER = logging.getLogger(__name__)


@dataclass
class Settings:
    isy_base_url: Optional[str]
    isy_username: Optional[str]
    isy_password: Optional[str]
    ntfy_publish_url: str
    ntfy_key: str


@dataclass
class MessageTemplate:
    template_id: int
    name: str
    body: str


class ISY2Ntfy:
    def __init__(self, settings: Settings, polyglot: Optional[object] = None) -> None:
        self.settings = settings
        self.session = requests.Session()
        self.session.verify = False

        # Attempt to auto-extract the system authentication token passed from PG3
        if polyglot and hasattr(polyglot, 'get_token'):
            token = polyglot.get_token()
            if token:
                LOGGER.info("Authenticating via local automated PG3 Bearer Token Headers.")
                self.session.headers.update({"Authorization": f"Bearer {token}"})
                return

        # Manual backup credentials
        if settings.isy_username and settings.isy_password:
            self.session.auth = (settings.isy_username, settings.isy_password)

    @classmethod
    def from_config_file(cls, config_path: str | Path) -> "ISY2Ntfy":
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        settings = Settings(
            isy_base_url=(config.get("isy_base_url") or "").rstrip("/") or None,
            isy_username=config.get("isy_username"),
            isy_password=config.get("isy_password"),
            ntfy_publish_url=str(config.get("ntfy_publish_url") or "https://ntfy.sh/isy2ntfy").rstrip("/"),
            ntfy_key=config["ntfy_key"],
        )
        return cls(settings)

    def fetch_customization_messages(self) -> List[MessageTemplate]:
        if not self.settings.isy_base_url:
            raise RuntimeError("ISY REST URL not configured for template fetch mode")

        # Probing array matching IoX version 6 paths
        endpoints = [
            "/rest/notification/customizations",
            "/rest/notifications/customizations",
            "/rest/notifications",
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
            except Exception as exc:  # pragma: no cover
                last_error = exc

        if last_error:
            raise RuntimeError(
                "Unable to fetch ISY notification customizations from known endpoints"
            ) from last_error
        return []

    def send_customization_to_ntfy(
        self,
        template_id: int,
        message_id: Optional[str] = None,
        tags: Optional[str] = None,
    ) -> requests.Response:
        templates = self.fetch_customization_messages()
        selected = next((t for t in templates if t.template_id == template_id), None)
        if selected is None:
            raise ValueError(f"Template ID {template_id} not found in ISY customizations")

        return self._publish_to_ntfy(selected, message_id=message_id, tags=tags)

    def build_template_options(self) -> Dict[int, str]:
        options: Dict[int, str] = {}
        for template in self.fetch_customization_messages():
            label = f"{template.template_id}: {template.name}".strip()
            options[template.template_id] = label
        return options

    def _publish_to_ntfy(
        self,
        template: MessageTemplate,
        message_id: Optional[str] = None,
        tags: Optional[str] = None,
    ) -> requests.Response:
        url = self._build_ntfy_publish_url(self.settings.ntfy_publish_url, self.settings.ntfy_key)

        effective_tags = (tags or "bell").strip() or "bell"
        headers = {
            "Title": template.name or "ISY Notification",
            "Tags": effective_tags,
        }
        if self._looks_like_token(self.settings.ntfy_key):
            headers["Authorization"] = f"Bearer {self.settings.ntfy_key}"
        if message_id:
            headers["X-ID"] = str(message_id)

        auth_enabled = "Authorization" in headers
        safe_headers = {k: v for k, v in headers.items() if k != "Authorization"}
        if auth_enabled:
            safe_headers["Authorization"] = "Bearer ***"

        LOGGER.info(
            "NTFY publish request: method=POST url=%s auth=%s message_id=%s headers=%s body_len=%s",
            url,
            auth_enabled,
            message_id or "",
            safe_headers,
            len(template.body.encode("utf-8")),
        )

        response = requests.post(url, data=template.body.encode("utf-8"), headers=headers, timeout=10)
        response.raise_for_status()
        LOGGER.info("NTFY publish response: status=%s url=%s", response.status_code, url)
        return response

    @staticmethod
    def _looks_like_token(value: str) -> bool:
        return (value or "").strip().startswith("tk_")

    @staticmethod
    def _build_ntfy_publish_url(raw_url: str, key: str) -> str:
        url = (raw_url or "").strip()
        if not url:
            url = "https://ntfy.sh"

        if "://" not in url:
            url = f"https://{url}"

        parsed = urlparse(url)
        base_path = parsed.path.rstrip("/")

        if base_path.endswith("/publish"):
            path = base_path[: -len("/publish")] or "/"
        elif base_path and base_path != "":
            path = base_path
        else:
            clean_key = (key or "").strip()
            if ISY2Ntfy._looks_like_token(clean_key):
                topic = "isy2ntfy"
            else:
                topic = clean_key or "isy2ntfy"
            path = f"/{quote(topic, safe='')}"

        normalized = parsed._replace(path=path, query="", fragment="")
        return urlunparse(normalized)

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