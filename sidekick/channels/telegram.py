"""Telegram channel — full duplex via the Bot API.

Outbound: `sendMessage`. Inbound: long-poll `getUpdates` (no public endpoint needed, so
this is the one channel that gives the gateway a complete loop on a laptop behind NAT).

Config (env):
  SIDEKICK_TELEGRAM_TOKEN   bot token from @BotFather
  SIDEKICK_TELEGRAM_CHAT_ID default chat id for outbound notifications
"""

from __future__ import annotations

import os
from collections.abc import Iterator

from .base import DELIVERY_POLL, Channel, InboundMessage, http_json

_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramChannel(Channel):
    name = "telegram"
    delivery = DELIVERY_POLL

    def __init__(self, token: str | None = None, chat_id: str | None = None):
        self.token = token or os.environ.get("SIDEKICK_TELEGRAM_TOKEN", "")
        self.chat_id = chat_id or os.environ.get("SIDEKICK_TELEGRAM_CHAT_ID", "")
        self._offset = 0  # getUpdates cursor — advanced past each consumed update

    @property
    def enabled(self) -> bool:
        return bool(self.token)

    def _url(self, method: str) -> str:
        return _API.format(token=self.token, method=method)

    def send(self, text: str, reply_to: str | None = None) -> bool:
        chat = reply_to or self.chat_id
        if not (self.enabled and chat):
            return False
        status, _ = http_json(
            self._url("sendMessage"),
            {"chat_id": chat, "text": text, "disable_web_page_preview": True},
        )
        return status == 200

    def poll(self) -> Iterator[InboundMessage]:
        if not self.enabled:
            return
        status, body = http_json(
            self._url("getUpdates"),
            {"offset": self._offset, "timeout": 20, "allowed_updates": ["message"]},
            timeout=30,
        )
        if status != 200 or not body.get("ok"):
            return
        for upd in body.get("result", []):
            self._offset = max(self._offset, int(upd.get("update_id", 0)) + 1)
            msg = upd.get("message") or {}
            text = (msg.get("text") or "").strip()
            chat = msg.get("chat") or {}
            if not text or not chat.get("id"):
                continue
            sender = (msg.get("from") or {}).get("username") or str((msg.get("from") or {}).get("id", ""))
            yield InboundMessage(
                channel=self.name,
                text=text,
                reply_to=str(chat["id"]),
                sender=sender,
                raw=upd,
            )
