"""Gateway — receive coding tasks over chat, run them, reply with the result.

The full Hermes 0.17 gateway shape, scoped to sidekick: inbound messages from every
configured channel are funneled into one queue and processed **sequentially** (orchestration
runs are heavy — serializing avoids worktree/merge contention). Each task is planned, run
through the orchestrator, and the result is replied back on the originating channel, with
live progress pushed via a reply-scoped Notifier.

Delivery styles are bridged uniformly:
  * poll channels (Telegram, iMessage) — a background thread drains `poll()` on a loop.
  * webhook channels (Slack, WhatsApp) — a small stdlib HTTP server receives callbacks and
    handles the verification handshakes.

SAFETY: inbound messages trigger auto-approved coding agents, so by default the gateway acts
only on senders in SIDEKICK_GATEWAY_ALLOW (comma-separated handles). Set SIDEKICK_GATEWAY_OPEN=1
to accept anyone (do this only on a trusted, private deployment).
"""

from __future__ import annotations

import asyncio
import os
import queue
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from ..approval import ApprovalPolicy
from ..config import Config
from ..planner import make_plan
from ..repo_context import gather
from .base import DELIVERY_POLL, DELIVERY_WEBHOOK, Channel, InboundMessage
from .notify import Notifier


class Gateway:
    def __init__(
        self,
        cfg: Config,
        channels: list[Channel],
        *,
        max_subtasks: int = 6,
        allow: list[str] | None = None,
        open_access: bool | None = None,
        http_host: str = "0.0.0.0",
        http_port: int = 8787,
    ):
        self.cfg = cfg
        self.channels = [c for c in channels if c.enabled]
        self.by_name = {c.name: c for c in self.channels}
        self.max_subtasks = max_subtasks
        self.inbox: queue.Queue[InboundMessage] = queue.Queue()
        self._stop = threading.Event()
        self.http_host = http_host
        self.http_port = http_port
        if allow is None:
            allow = [a.strip() for a in os.environ.get("SIDEKICK_GATEWAY_ALLOW", "").split(",") if a.strip()]
        self.allow = set(allow)
        self.open_access = (
            open_access
            if open_access is not None
            else os.environ.get("SIDEKICK_GATEWAY_OPEN", "") in ("1", "true", "yes")
        )

    # -- authorization --------------------------------------------------------

    def _authorized(self, msg: InboundMessage) -> bool:
        if self.open_access:
            return True
        if not self.allow:
            return False  # closed by default — no allowlist means nobody
        return msg.sender in self.allow or msg.reply_to in self.allow

    # -- inbound sources ------------------------------------------------------

    def _poll_loop(self, chan: Channel) -> None:
        while not self._stop.is_set():
            try:
                for msg in chan.poll():
                    self.inbox.put(msg)
            except Exception:  # noqa: BLE001 — a transient poll error must not kill the loop
                self._stop.wait(2.0)

    def _make_http_handler(self):
        webhook_channels = [c for c in self.channels if c.delivery == DELIVERY_WEBHOOK]
        gateway = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):  # silence default stderr logging
                pass

            def do_GET(self):  # verification handshakes (e.g. WhatsApp hub.challenge)
                query = {k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}
                for c in webhook_channels:
                    challenge = c.webhook_challenge(query)
                    if challenge is not None:
                        self._respond(200, challenge)
                        return
                self._respond(200, "ok")

            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0) or 0)
                body = self.rfile.read(length) if length else b""
                headers = {k.lower(): v for k, v in self.headers.items()}
                reply = "ok"
                for c in webhook_channels:
                    for msg in c.parse_webhook(headers, body):
                        if gateway._authorized(msg):
                            gateway.inbox.put(msg)
                    # Slack url_verification: echo the challenge back in the response.
                    take = getattr(c, "take_challenge", None)
                    if take:
                        ch = take()
                        if ch:
                            reply = ch
                self._respond(200, reply)

            def _respond(self, code: int, text: str):
                payload = text.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/plain")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        return Handler

    # -- task execution -------------------------------------------------------

    def _handle(self, msg: InboundMessage) -> None:
        chan = self.by_name.get(msg.channel)
        if chan is None:
            return
        if not self._authorized(msg):
            chan.send(
                "⛔ Not authorized. Ask the operator to add you to SIDEKICK_GATEWAY_ALLOW.",
                reply_to=msg.reply_to,
            )
            return
        task = msg.text.strip()
        if not task:
            return
        chan.send(f"🛠 Working on: {task}", reply_to=msg.reply_to)
        notifier = Notifier([chan], reply_to={chan.name: msg.reply_to})
        try:
            ctx = gather(self.cfg.repo_root)
            plan = make_plan(self.cfg, ctx, task, max_subtasks=self.max_subtasks)
            policy = ApprovalPolicy(level=self.cfg.approval)
            from ..orchestrator import Orchestrator

            report = asyncio.run(
                Orchestrator(self.cfg, policy, notifier=notifier).run(plan, ctx, mode="orchestrated")
            )
        except Exception as e:  # noqa: BLE001 — report failures back to the requester
            chan.send(f"💥 Run failed: {e}", reply_to=msg.reply_to)
            return
        chan.send(
            f"✅ Done: {report.n_accepted}/{len(report.outcomes)} accepted, "
            f"{report.n_merged} merged, wall {report.wall_ms / 1000:.1f}s",
            reply_to=msg.reply_to,
        )

    # -- run loop -------------------------------------------------------------

    def serve_forever(self) -> None:
        if not self.channels:
            raise RuntimeError("no enabled channels — configure tokens (see channels docs)")
        threads: list[threading.Thread] = []
        for c in self.channels:
            if c.delivery == DELIVERY_POLL:
                t = threading.Thread(target=self._poll_loop, args=(c,), daemon=True)
                t.start()
                threads.append(t)

        httpd = None
        if any(c.delivery == DELIVERY_WEBHOOK for c in self.channels):
            httpd = ThreadingHTTPServer((self.http_host, self.http_port), self._make_http_handler())
            threading.Thread(target=httpd.serve_forever, daemon=True).start()

        try:
            while not self._stop.is_set():
                try:
                    msg = self.inbox.get(timeout=1.0)
                except queue.Empty:
                    continue
                self._handle(msg)
        except KeyboardInterrupt:
            pass
        finally:
            self._stop.set()
            if httpd is not None:
                httpd.shutdown()

    def stop(self) -> None:
        self._stop.set()
