"""iMessage channel — relay bridge.

iMessage has no public API and is not self-hostable on its own (Hermes uses a paid
"Photon Spectrum" relay). This adapter is therefore a *bridge* to whatever relay you run on
a Mac — BlueBubbles, an AppleScript poller, a Shortcuts automation, etc.:

  * outbound — `send` shells out to a command template you configure. `{to}` is substituted
    with the recipient and the message body is piped on stdin. Example for AppleScript:
        SIDEKICK_IMESSAGE_SEND_CMD='osascript send_imessage.applescript {to}'
  * inbound  — `poll` tails a newline-delimited JSON inbox file your relay appends to. Each
    line is an object like {"text": "...", "from": "+1555..."}. A byte offset is tracked so
    each message is read once.

Config (env):
  SIDEKICK_IMESSAGE_SEND_CMD   send command template (contains {to}); body on stdin
  SIDEKICK_IMESSAGE_INBOX      path to the relay's JSONL inbox file
  SIDEKICK_IMESSAGE_DEFAULT_TO default recipient handle for notifications
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from collections.abc import Iterator
from pathlib import Path

from .base import DELIVERY_POLL, Channel, InboundMessage


class IMessageChannel(Channel):
    name = "imessage"
    delivery = DELIVERY_POLL

    def __init__(
        self,
        send_cmd: str | None = None,
        inbox: str | None = None,
        default_to: str | None = None,
    ):
        self.send_cmd = send_cmd if send_cmd is not None else os.environ.get("SIDEKICK_IMESSAGE_SEND_CMD", "")
        self.inbox = inbox if inbox is not None else os.environ.get("SIDEKICK_IMESSAGE_INBOX", "")
        self.default_to = default_to or os.environ.get("SIDEKICK_IMESSAGE_DEFAULT_TO", "")
        self._offset = 0  # bytes consumed from the inbox file

    @property
    def enabled(self) -> bool:
        # Enabled if it can do *either* direction.
        return bool(self.send_cmd or self.inbox)

    def send(self, text: str, reply_to: str | None = None) -> bool:
        to = reply_to or self.default_to
        if not (self.send_cmd and to):
            return False
        cmd = [part.replace("{to}", to) for part in shlex.split(self.send_cmd)]
        try:
            proc = subprocess.run(
                cmd, input=text, text=True, capture_output=True, timeout=30
            )
            return proc.returncode == 0
        except (subprocess.SubprocessError, OSError):
            return False

    def poll(self) -> Iterator[InboundMessage]:
        if not self.inbox:
            return
        path = Path(self.inbox)
        if not path.exists():
            return
        try:
            with path.open("r", encoding="utf-8") as f:
                f.seek(self._offset)
                chunk = f.read()
                self._offset = f.tell()
        except OSError:
            return
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            text = str(rec.get("text", "")).strip()
            frm = str(rec.get("from", ""))
            if not text or not frm:
                continue
            yield InboundMessage(channel=self.name, text=text, reply_to=frm, sender=frm, raw=rec)
