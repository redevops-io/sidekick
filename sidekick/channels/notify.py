"""Outbound notifier — fan run lifecycle events out to configured channels.

Wired into the orchestrator as an optional, best-effort observer: a channel that errors or
is misconfigured never affects the run. Mirrors Hermes 0.17's "live subagent monitoring"
but pushed to chat instead of a desktop window.
"""

from __future__ import annotations

from .base import Channel


class Notifier:
    """Formats and broadcasts run events to a list of channels (outbound only)."""

    def __init__(self, channels: list[Channel], reply_to: dict[str, str] | None = None):
        # Only keep channels that can actually send.
        self.channels = [c for c in channels if c.enabled]
        # Optional per-channel destination override (channel name -> reply_to handle);
        # used by the gateway so progress goes back to the requester's thread.
        self.reply_to = reply_to or {}

    @property
    def active(self) -> bool:
        return bool(self.channels)

    def _broadcast(self, text: str) -> None:
        for c in self.channels:
            try:
                c.send(text, reply_to=self.reply_to.get(c.name))
            except Exception:  # noqa: BLE001 — notifications are strictly best-effort
                pass

    def run_started(self, task: str, n_subtasks: int, backend: str) -> None:
        self._broadcast(
            f"🚀 sidekick started\nTask: {task}\nSubtasks: {n_subtasks} · backend: {backend}"
        )

    def subtask_done(self, subtask_id: str, accepted: bool, merged: bool, attempts: int) -> None:
        mark = "✅" if accepted else "❌"
        self._broadcast(
            f"{mark} [{subtask_id}] {'accepted' if accepted else 'failed'} "
            f"· attempts={attempts} · merged={merged}"
        )

    def run_finished(self, task: str, n_accepted: int, n_total: int, n_merged: int, wall_ms: int) -> None:
        ok = n_accepted == n_total
        self._broadcast(
            f"{'🎉' if ok else '⚠️'} sidekick finished\nTask: {task}\n"
            f"{n_accepted}/{n_total} accepted · {n_merged} merged · wall {wall_ms / 1000:.1f}s"
        )
