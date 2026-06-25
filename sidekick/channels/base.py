"""Channel abstraction (ported from the Hermes 0.17 gateway).

A `Channel` is a bidirectional bridge to a messaging platform. Two capabilities, either of
which an adapter may support:

  * **outbound** — `send(text, thread=...)` pushes a message (used by the notifier to report
    run start / progress / result).
  * **inbound** — how the gateway *receives* a task. Two delivery styles:
      - poll  : the adapter long-polls the platform itself (Telegram getUpdates). Implement
                `poll()` to yield InboundMessage objects.
      - webhook: the platform calls us. Implement `parse_webhook(headers, body)` to turn an
                 HTTP callback into InboundMessages; the shared webhook server (gateway.py)
                 owns the socket.

Adapters are deliberately dependency-free — all HTTP is stdlib urllib — so the whole
feature runs on a $5 VPS with nothing but Python, matching Hermes' "run it anywhere" goal.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass, field

# How a channel receives inbound messages.
DELIVERY_NONE = "none"  # outbound only
DELIVERY_POLL = "poll"  # adapter long-polls the platform
DELIVERY_WEBHOOK = "webhook"  # platform calls our HTTP endpoint


@dataclass
class InboundMessage:
    """A task request received from a channel."""

    channel: str
    text: str
    # Opaque routing handle the adapter uses to reply (chat id, channel id, phone number…).
    reply_to: str
    sender: str = ""
    raw: dict = field(default_factory=dict)


class Channel:
    """Base class for messaging adapters. Subclasses set `name` and override what they
    support. The default methods are safe no-ops so a partially-capable adapter (e.g.
    outbound-only) needs to implement only what it offers."""

    name: str = "channel"
    delivery: str = DELIVERY_NONE

    @property
    def enabled(self) -> bool:
        """True when the adapter has the credentials it needs to operate."""
        return False

    def send(self, text: str, reply_to: str | None = None) -> bool:
        """Push an outbound message. Returns True on success. Best-effort: never raises."""
        return False

    def poll(self) -> Iterator[InboundMessage]:
        """Yield inbound messages (poll-delivery adapters only). One drain per call."""
        return iter(())

    def parse_webhook(self, headers: dict, body: bytes) -> list[InboundMessage]:
        """Translate an HTTP callback into inbound messages (webhook-delivery adapters)."""
        return []

    def webhook_challenge(self, query: dict) -> str | None:
        """Optional verification handshake (e.g. WhatsApp hub.challenge echo)."""
        return None


def http_json(
    url: str,
    payload: dict | None = None,
    headers: dict | None = None,
    method: str | None = None,
    timeout: int = 30,
) -> tuple[int, dict]:
    """Minimal stdlib JSON HTTP helper. Returns (status, parsed_body). Never raises on
    HTTP/transport errors — returns (0, {"error": ...}) so callers stay best-effort."""
    data = None
    hdrs = dict(headers or {})
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")
    req = urllib.request.Request(url, data=data, headers=hdrs, method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8") or "{}"
            try:
                return resp.status, json.loads(raw)
            except json.JSONDecodeError:
                return resp.status, {"raw": raw}
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8"))
        except (json.JSONDecodeError, OSError, ValueError):
            body = {}
        return e.code, {"error": str(e), **body}
    except (urllib.error.URLError, OSError, ValueError) as e:
        return 0, {"error": str(e)}
