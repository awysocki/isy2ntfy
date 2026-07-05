from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, List, Optional
import xml.etree.ElementTree as ET
from urllib.parse import quote, urlparse, urlunparse

import requests

try:
    import udi_interface  # type: ignore

    LOGGER = udi_interface.LOGGER
except Exception:  # pragma: no cover - fallback for local runs without udi_interface
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

    def fetch_customization_messages(self) -> List[MessageTemplate]:
        """Returns ISY customization messages from the first compatible endpoint.

        Different ISY firmware builds can expose this in slightly different paths.
        We try several known endpoints and parse XML or JSON.
        """
        if not self.settings.isy_base_url:
            raise RuntimeError("ISY REST URL not configured for template fetch mode")

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
        """Returns template choices for a PG3 editor dropdown/selector mapping."""
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
        """Build a POST topic URL from base URL and KEY.

        Supported inputs include:
        - URL: https://ntfy.sh, KEY as topic -> https://ntfy.sh/<KEY>
        - URL: https://ntfy.sh, KEY as token -> https://ntfy.sh/isy2ntfy
        - URL already including topic path -> use that topic path directly
        """
        url = (raw_url or "").strip()
        if not url:
            url = "https://ntfy.sh"

        # Accept bare host values by defaulting to https.
        if "://" not in url:
            url = f"https://{url}"

        parsed = urlparse(url)
        base_path = parsed.path.rstrip("/")

        if base_path.endswith("/publish"):
            # Accept older config values, but publish to plain topic endpoint.
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

        # Drop query/fragment so runtime message body is always the payload.
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
