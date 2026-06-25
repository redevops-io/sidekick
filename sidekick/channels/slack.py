"""Slack channel.

Outbound: `chat.postMessage` with a bot token (xoxb-…). Inbound: the Events API, delivered
as an HTTP webhook (the shared gateway server owns the socket). Slack's url_verification
handshake and message events are handled in `parse_webhook` / `webhook_challenge`.

Config (env):
  SIDEKICK_SLACK_BOT_TOKEN   xoxb-… bot token (outbound)
  SIDEKICK_SLACK_CHANNEL     default channel id for notifications
  SIDEKICK_SLACK_SIGNING_*   (verification is out of scope here; run behind a trusted proxy)
"""

from __future__ import annotations

import json
import os

from .base import DELIVERY_WEBHOOK, Channel, InboundMessage, http_json

_POST = "https://slack.com/api/chat.postMessage"


class SlackChannel(Channel):
    name = "slack"
    delivery = DELIVERY_WEBHOOK

    def __init__(self, token: str | None = None, channel: str | None = None):
        self.token = token or os.environ.get("SIDEKICK_SLACK_BOT_TOKEN", "")
        self.channel = channel or os.environ.get("SIDEKICK_SLACK_CHANNEL", "")

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def send(self, text: str, reply_to: str | None = None) -> bool:
        chan = reply_to or self.channel
        if not (self.enabled and chan):
            return False
        status, body = http_json(
            _POST,
            {"channel": chan, "text": text},
            headers={"Authorization": f"Bearer {self.token}"},
        )
        return status == 200 and bool(body.get("ok"))

    def webhook_challenge(self, query: dict) -> str | None:
        return None  # Slack uses a body-based handshake, handled in parse_webhook.

    def parse_webhook(self, headers: dict, body: bytes) -> list[InboundMessage]:
        try:
            data = json.loads(body.decode("utf-8") or "{}")
        except (json.JSONDecodeError, UnicodeDecodeError):
            return []
        # url_verification handshake: echo the challenge (surfaced via _challenge attr).
        if data.get("type") == "url_verification":
            self._pending_challenge = data.get("challenge", "")
            return []
        event = data.get("event") or {}
        # Only act on human messages — ignore bot echoes and edits to avoid loops.
        if event.get("type") != "message" or event.get("bot_id") or event.get("subtype"):
            return []
        text = (event.get("text") or "").strip()
        chan = event.get("channel") or ""
        if not text or not chan:
            return []
        return [
            InboundMessage(
                channel=self.name,
                text=text,
                reply_to=chan,
                sender=event.get("user", ""),
                raw=data,
            )
        ]

    # The gateway server checks this after parse_webhook to satisfy url_verification.
    _pending_challenge: str = ""

    def take_challenge(self) -> str:
        c, self._pending_challenge = self._pending_challenge, ""
        return c
