from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import quote, urlparse, urlunparse

import requests

try:
    import udi_interface  # type: ignore
    LOGGER = udi_interface.LOGGER
except Exception:  # pragma: no cover
    LOGGER = logging.getLogger(__name__)


@dataclass
class Settings:
    ntfy_publish_url: str
    ntfy_key: str


class ISY2Ntfy:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @classmethod
    def from_config_file(cls, config_path: str | Path) -> "ISY2Ntfy":
        config = json.loads(Path(config_path).read_text(encoding="utf-8"))
        settings = Settings(
            ntfy_publish_url=str(config.get("ntfy_publish_url") or "https://ntfy.sh/isy2ntfy").rstrip("/"),
            ntfy_key=config["ntfy_key"],
        )
        return cls(settings)

    def send_notification(
        self,
        title: str,
        body: str,
        message_id: Optional[str] = None,
        tags: Optional[str] = None,
    ) -> requests.Response:
        """Takes a raw title and body string and pushes it to the ntfy.sh server."""
        url = self._build_ntfy_publish_url(self.settings.ntfy_publish_url, self.settings.ntfy_key)

        effective_tags = (tags or "bell").strip() or "bell"
        headers = {
            "Title": title or "ISY Notification",
            "Tags": effective_tags,
        }
        
        # Handle auth token if present
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
            len(body.encode("utf-8")),
        )

        response = requests.post(url, data=body.encode("utf-8"), headers=headers, timeout=10)
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