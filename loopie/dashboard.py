"""Live progress dashboard across all branched agents.

Renders a per-agent table (status, current action, edits, turns, tokens, elapsed) via
`rich`. Degrades gracefully to periodic plain-text lines when stdout is not a TTY or rich
is unavailable, so loopie still works in CI/logs.
"""

from __future__ import annotations

import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from . import events as ev

try:  # rich is a declared dependency but keep a hard fallback.
    from rich.console import Console
    from rich.live import Live
    from rich.table import Table

    _HAS_RICH = True
except Exception:  # pragma: no cover
    _HAS_RICH = False


@dataclass
class AgentView:
    name: str
    status: str = "queued"
    action: str = ""
    edits: int = 0
    tools: int = 0
    turns: int = 0
    tokens: int = 0
    started: float | None = None
    finished: float | None = None
    ok: bool | None = None

    def elapsed(self) -> float:
        if self.started is None:
            return 0.0
        end = self.finished or time.monotonic()
        return end - self.started


_STATUS_STYLE = {
    "queued": "dim",
    "running": "yellow",
    "checking": "cyan",
    "done": "green",
    "failed": "red",
    "timeout": "red",
}

_STATUS_EMOJI = {
    "queued": "⏳",
    "running": "🔄",
    "checking": "🔎",
    "done": "✅",
    "failed": "❌",
    "timeout": "⏱️",
}


class Dashboard:
    def __init__(
        self,
        title: str,
        use_rich: bool | None = None,
        progress_path: Path | None = None,
        concurrency: int | None = None,
    ):
        self.title = title
        self.agents: dict[str, AgentView] = {}
        self._lock = threading.Lock()
        self._live = None
        self._last_plain = 0.0
        # Live markdown mirror of the dashboard, for VSCode / any editor to render.
        self.progress_path = Path(progress_path) if progress_path else None
        self.concurrency = concurrency
        self._last_md = 0.0
        if use_rich is None:
            use_rich = _HAS_RICH and sys.stdout.isatty()
        self.use_rich = use_rich
        self._console = Console() if _HAS_RICH else None

    def register(self, name: str) -> None:
        with self._lock:
            self.agents.setdefault(name, AgentView(name=name))

    def set_status(self, name: str, status: str, action: str = "") -> None:
        with self._lock:
            a = self.agents.setdefault(name, AgentView(name=name))
            a.status = status
            if action:
                a.action = action
            if status == "running" and a.started is None:
                a.started = time.monotonic()
            if status in ("done", "failed", "timeout"):
                a.finished = time.monotonic()
                a.ok = status == "done"
        self._refresh()

    def on_event(self, name: str, event: ev.Event) -> None:
        with self._lock:
            a = self.agents.setdefault(name, AgentView(name=name))
            if a.started is None:
                a.started = time.monotonic()
            if event.kind == ev.TOOL_USE:
                a.tools += 1
                a.action = _describe_tool(event)
                if event.tool_name in ("Edit", "Write", "NotebookEdit"):
                    a.edits += 1
            elif event.kind == ev.TEXT and event.text:
                a.action = _clip_oneline(event.text)
            elif event.kind == ev.RESULT:
                a.turns = event.num_turns or a.turns
                if event.usage:
                    a.tokens = (
                        event.usage.get("input", 0)
                        + event.usage.get("output", 0)
                        + event.usage.get("cache_read", 0)
                        + event.usage.get("cache_creation", 0)
                    )
        self._refresh()

    # -- rendering --------------------------------------------------------------
    def _build_table(self):  # pragma: no cover - visual
        table = Table(title=self.title, expand=True)
        table.add_column("agent", style="bold")
        table.add_column("status")
        table.add_column("action", overflow="ellipsis", max_width=48)
        table.add_column("edits", justify="right")
        table.add_column("turns", justify="right")
        table.add_column("tokens", justify="right")
        table.add_column("elapsed", justify="right")
        for a in self.agents.values():
            style = _STATUS_STYLE.get(a.status, "white")
            table.add_row(
                a.name,
                f"[{style}]{a.status}[/{style}]",
                a.action,
                str(a.edits),
                str(a.turns),
                f"{a.tokens:,}",
                f"{a.elapsed():.0f}s",
            )
        return table

    def render_markdown(self, footer: str = "") -> str:
        done = sum(1 for a in self.agents.values() if a.status == "done")
        failed = sum(1 for a in self.agents.values() if a.status in ("failed", "timeout"))
        conc = f" · concurrency {self.concurrency}" if self.concurrency else ""
        lines = [
            f"# {self.title}",
            "",
            f"**{done} done · {failed} failed · {len(self.agents)} agents{conc}**",
            "",
            "| agent | status | action | edits | turns | tokens | elapsed |",
            "|-------|--------|--------|------:|------:|-------:|--------:|",
        ]
        for a in self.agents.values():
            emoji = _STATUS_EMOJI.get(a.status, "")
            action = (a.action or "").replace("|", "\\|")
            lines.append(
                f"| `{a.name}` | {emoji} {a.status} | {action} | {a.edits} | {a.turns} "
                f"| {a.tokens:,} | {a.elapsed():.0f}s |"
            )
        if footer:
            lines += ["", footer]
        return "\n".join(lines) + "\n"

    def _write_markdown(self, force: bool = False, footer: str = "") -> None:
        if self.progress_path is None:
            return
        now = time.monotonic()
        if not force and now - self._last_md < 0.5:
            return
        self._last_md = now
        try:
            self.progress_path.parent.mkdir(parents=True, exist_ok=True)
            self.progress_path.write_text(self.render_markdown(footer), encoding="utf-8")
        except OSError:
            pass

    def _refresh(self) -> None:
        if self.use_rich and self._live is not None:  # pragma: no cover - visual
            try:
                self._live.update(self._build_table())
            except Exception:
                pass
        elif not self.use_rich:
            now = time.monotonic()
            if now - self._last_plain > 2.0:
                self._last_plain = now
                self._print_plain()
        self._write_markdown()

    def _print_plain(self) -> None:
        parts = []
        for a in self.agents.values():
            parts.append(f"{a.name}:{a.status}({a.edits}e/{a.turns}t)")
        print(f"[loopie] {' | '.join(parts)}", flush=True)

    def finalize(self, footer: str = "") -> None:
        """Force a final write of the progress markdown (e.g. with a result footer)."""
        self._write_markdown(force=True, footer=footer)

    def __enter__(self) -> Dashboard:
        self._write_markdown(force=True)
        if self.use_rich:  # pragma: no cover - visual
            self._live = Live(self._build_table(), console=self._console, refresh_per_second=4)
            self._live.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        if self.use_rich and self._live is not None:  # pragma: no cover - visual
            try:
                self._live.update(self._build_table())
            finally:
                self._live.__exit__(*exc)
        else:
            self._print_plain()
        self._write_markdown(force=True)


def _clip_oneline(text: str, n: int = 60) -> str:
    line = " ".join(text.split())
    return line if len(line) <= n else line[: n - 1] + "…"


def _describe_tool(event: ev.Event) -> str:
    name = event.tool_name or "tool"
    inp = event.tool_input or {}
    if name in ("Edit", "Write", "Read"):
        return f"{name} {inp.get('file_path', '')}".strip()
    if name == "Bash":
        return f"Bash: {_clip_oneline(str(inp.get('command', '')), 40)}"
    if name in ("Grep", "Glob"):
        return f"{name} {inp.get('pattern', '')}"
    return name
