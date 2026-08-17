from __future__ import annotations

import mimetypes
from pathlib import Path
from typing import Any

import requests

from app.config import settings


class WhatsAppClient:
    """Small wrapper around the official WhatsApp Cloud API."""

    def __init__(self):
        self.version = settings.whatsapp_graph_version
        self.phone_number_id = settings.whatsapp_phone_number_id
        self.token = settings.whatsapp_access_token

    @property
    def base_url(self) -> str:
        return f"https://graph.facebook.com/{self.version}"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    def _ensure_configured(self):
        missing = []
        if not self.phone_number_id:
            missing.append("WHATSAPP_PHONE_NUMBER_ID")
        if not self.token:
            missing.append("WHATSAPP_ACCESS_TOKEN")
        if missing:
            raise RuntimeError("Missing WhatsApp configuration: " + ", ".join(missing))

    def send_text(self, recipient: str, body: str) -> dict[str, Any]:
        self._ensure_configured()
        url = f"{self.base_url}/{self.phone_number_id}/messages"
        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "text",
            "text": {"preview_url": False, "body": body},
        }
        response = requests.post(url, headers={**self._headers(), "Content-Type": "application/json"}, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()

    def upload_media(self, path: str) -> str:
        self._ensure_configured()
        url = f"{self.base_url}/{self.phone_number_id}/media"
        mime = mimetypes.guess_type(path)[0] or "image/png"
        with open(path, "rb") as fh:
            files = {"file": (Path(path).name, fh, mime)}
            data = {"messaging_product": "whatsapp", "type": mime}
            response = requests.post(url, headers=self._headers(), files=files, data=data, timeout=60)
        response.raise_for_status()
        return response.json()["id"]

    def send_image(self, recipient: str, image_path: str, caption: str | None = None) -> dict[str, Any]:
        media_id = self.upload_media(image_path)
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": recipient,
            "type": "image",
            "image": {"id": media_id},
        }
        if caption:
            payload["image"]["caption"] = caption
        url = f"{self.base_url}/{self.phone_number_id}/messages"
        response = requests.post(url, headers={**self._headers(), "Content-Type": "application/json"}, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()

    def send_template(self, recipient: str, template_name: str, language: str, body_params: list[str] | None = None) -> dict[str, Any]:
        self._ensure_configured()
        components = []
        if body_params:
            components.append({
                "type": "body",
                "parameters": [{"type": "text", "text": str(v)} for v in body_params],
            })
        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": recipient,
            "type": "template",
            "template": {
                "name": template_name,
                "language": {"code": language},
            },
        }
        if components:
            payload["template"]["components"] = components
        url = f"{self.base_url}/{self.phone_number_id}/messages"
        response = requests.post(url, headers={**self._headers(), "Content-Type": "application/json"}, json=payload, timeout=30)
        response.raise_for_status()
        return response.json()
