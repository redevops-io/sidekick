"""WhatsApp channel — Meta WhatsApp Business Cloud API.

Outbound: POST a text message to `/{phone_number_id}/messages`. Inbound: Meta delivers
messages as an HTTP webhook with a GET verification handshake (hub.challenge) followed by
POST callbacks — both handled here; the shared gateway server owns the socket.

Note: WhatsApp is *cloud-bound* — it requires a Meta Business account, an approved phone
number, and a publicly reachable webhook URL. Unlike Telegram there is no polling fallback.

Config (env):
  SIDEKICK_WHATSAPP_TOKEN          Cloud API access token
  SIDEKICK_WHATSAPP_PHONE_ID       phone number id (sender)
  SIDEKICK_WHATSAPP_VERIFY_TOKEN   the verify token you set in the Meta webhook config
  SIDEKICK_WHATSAPP_DEFAULT_TO     default recipient (E.164) for notifications
"""

from __future__ import annotations

import json
import os

from .base import DELIVERY_WEBHOOK, Channel, InboundMessage, http_json

_API = "https://graph.facebook.com/v20.0/{phone_id}/messages"


class WhatsAppChannel(Channel):
    name = "whatsapp"
    delivery = DELIVERY_WEBHOOK

    def __init__(
        self,
        token: str | None = None,
        phone_id: str | None = None,
        verify_token: str | None = None,
        default_to: str | None = None,
    ):
        self.token = token or os.environ.get("SIDEKICK_WHATSAPP_TOKEN", "")
        self.phone_id = phone_id or os.environ.get("SIDEKICK_WHATSAPP_PHONE_ID", "")
        self.verify_token = verify_token or os.environ.get("SIDEKICK_WHATSAPP_VERIFY_TOKEN", "")
        self.default_to = default_to or os.environ.get("SIDEKICK_WHATSAPP_DEFAULT_TO", "")

    @property
    def enabled(self) -> bool:
        return bool(self.token and self.phone_id)

    def send(self, text: str, reply_to: str | None = None) -> bool:
        to = reply_to or self.default_to
        if not (self.enabled and to):
            return False
        status, _ = http_json(
            _API.format(phone_id=self.phone_id),
            {
                "messaging_product": "whatsapp",
                "to": to,
                "type": "text",
                "text": {"body": text},
            },
            headers={"Authorization": f"Bearer {self.token}"},
        )
        return status in (200, 201)

    def webhook_challenge(self, query: dict) -> str | None:
        """Meta GET handshake: echo hub.challenge iff the verify token matches."""
        if query.get("hub.mode") == "subscribe" and query.get("hub.verify_token") == self.verify_token:
            return query.get("hub.challenge")
        return None

    def parse_webhook(self, headers: dict, body: bytes) -> list[InboundMessage]:
        try:
            data = json.loads(body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return []
        out: list[InboundMessage] = []
        for entry in data.get("entry", []):
            for change in entry.get("changes", []):
                value = change.get("value") or {}
                for msg in value.get("messages", []):
                    if msg.get("type") != "text":
                        continue
                    text = (msg.get("text") or {}).get("body", "").strip()
                    frm = msg.get("from", "")
                    if not text or not frm:
                        continue
                    out.append(
                        InboundMessage(
                            channel=self.name, text=text, reply_to=frm, sender=frm, raw=data
                        )
                    )
        return out
