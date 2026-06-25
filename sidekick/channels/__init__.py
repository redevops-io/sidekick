"""Communication channels (ported from the Hermes 0.17 gateway).

Bidirectional bridges to messaging platforms — Telegram, Slack, WhatsApp, iMessage — used
two ways:

  * notify   : push run start / progress / result outbound (see notify.Notifier)
  * gateway  : receive a coding task inbound, run an orchestration, reply with the result
               (see gateway.Gateway)

`load_channels()` builds the set named by SIDEKICK_CHANNELS (default: all that are
configured) and keeps only the ones with credentials present.
"""

from __future__ import annotations

import os

from .base import (
    DELIVERY_NONE,
    DELIVERY_POLL,
    DELIVERY_WEBHOOK,
    Channel,
    InboundMessage,
)
from .imessage import IMessageChannel
from .notify import Notifier
from .slack import SlackChannel
from .telegram import TelegramChannel
from .whatsapp import WhatsAppChannel

_REGISTRY: dict[str, type[Channel]] = {
    "telegram": TelegramChannel,
    "slack": SlackChannel,
    "whatsapp": WhatsAppChannel,
    "imessage": IMessageChannel,
}


def available_channel_names() -> list[str]:
    return list(_REGISTRY)


def load_channels(names: list[str] | None = None, *, only_enabled: bool = True) -> list[Channel]:
    """Instantiate channels.

    `names` selects which adapters to build (default: SIDEKICK_CHANNELS env, comma-
    separated, or all). With `only_enabled` (the default) channels lacking credentials are
    dropped, so the caller gets exactly the working set.
    """
    if names is None:
        env = os.environ.get("SIDEKICK_CHANNELS", "").strip()
        names = [n.strip() for n in env.split(",") if n.strip()] or list(_REGISTRY)
    chans: list[Channel] = []
    for n in names:
        cls = _REGISTRY.get(n.lower())
        if cls is None:
            continue
        c = cls()
        if not only_enabled or c.enabled:
            chans.append(c)
    return chans


def make_notifier(names: list[str] | None = None, reply_to: dict[str, str] | None = None) -> Notifier:
    return Notifier(load_channels(names), reply_to=reply_to)


__all__ = [
    "DELIVERY_NONE",
    "DELIVERY_POLL",
    "DELIVERY_WEBHOOK",
    "Channel",
    "InboundMessage",
    "Notifier",
    "TelegramChannel",
    "SlackChannel",
    "WhatsAppChannel",
    "IMessageChannel",
    "available_channel_names",
    "load_channels",
    "make_notifier",
]
