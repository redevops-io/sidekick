"""Wrap a single auto-approved Claude Code headless session (Raschka #3).

Spawns `claude -p --output-format stream-json`, streams normalized events to a callback
(for the live dashboard), enforces auto-approval via the chosen ApprovalPolicy, and
accumulates an AgentResult with the speed/accuracy/efficiency signals loopie optimizes.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import events as ev
from .approval import ApprovalPolicy
from .config import Config

EventCallback = Callable[[str, ev.Event], None]


@dataclass
class AgentResult:
    name: str
    session_id: str | None = None
    success: bool = False
    timed_out: bool = False
    error: str | None = None
    # Timing.
    wall_ms: int = 0
    duration_ms: int | None = None  # model-reported
    ttft_ms: int | None = None
    time_to_first_edit_ms: int | None = None  # loopie-measured (S3)
    num_turns: int | None = None
    # Tools.
    tool_calls: int = 0
    edits: int = 0
    bash_calls: int = 0
    # Efficiency.
    cost_usd: float = 0.0
    tokens: dict[str, int] = field(default_factory=lambda: {"input": 0, "output": 0, "cache_read": 0, "cache_creation": 0})
    final_text: str = ""

    @property
    def cache_hit_ratio(self) -> float:
        denom = self.tokens["input"] + self.tokens["cache_read"] + self.tokens["cache_creation"]
        return (self.tokens["cache_read"] / denom) if denom else 0.0


def build_command(
    cfg: Config,
    policy: ApprovalPolicy,
    prompt: str,
    session_id: str,
    model: str | None,
    append_system: str | None = None,
) -> list[str]:
    cmd = [
        cfg.claude_bin,
        "-p",
        prompt,
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        policy.permission_mode,
        "--session-id",
        session_id,
        "--max-turns",
        str(cfg.agent_max_turns),
    ]
    if append_system:
        cmd += ["--append-system-prompt", append_system]
    if policy.requires_dangerous_flag:
        cmd.append("--allow-dangerously-skip-permissions")
    allowed = policy.allowed_tools()
    if allowed:
        cmd += ["--allowedTools", " ".join(allowed)]
    disallowed = policy.disallowed_tools()
    if disallowed:
        cmd += ["--disallowedTools", " ".join(disallowed)]
    if model:
        cmd += ["--model", model]
    return cmd


async def run_agent(
    cfg: Config,
    policy: ApprovalPolicy,
    name: str,
    prompt: str,
    cwd: Path,
    on_event: EventCallback | None = None,
    model: str | None = None,
    append_system: str | None = None,
) -> AgentResult:
    """Run one headless agent session in `cwd`, streaming events to on_event."""
    session_id = str(uuid.uuid4())
    cmd = build_command(cfg, policy, prompt, session_id, model or cfg.agent_model, append_system)
    result = AgentResult(name=name, session_id=session_id)
    start = time.monotonic()

    env = dict(os.environ)
    env["CLAUDE_CODE_EXECPATH"] = cfg.claude_bin

    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
    except OSError as e:
        result.error = f"spawn failed: {e}"
        result.wall_ms = int((time.monotonic() - start) * 1000)
        return result

    async def pump() -> None:
        assert proc.stdout is not None
        async for raw in proc.stdout:
            line = raw.decode("utf-8", errors="replace")
            for event in ev.parse_line(line):
                _apply(result, event, start)
                if on_event is not None:
                    on_event(name, event)

    try:
        await asyncio.wait_for(pump(), timeout=cfg.agent_timeout_s)
        await asyncio.wait_for(proc.wait(), timeout=30)
    except TimeoutError:
        result.timed_out = True
        result.error = f"timed out after {cfg.agent_timeout_s}s"
        try:
            proc.kill()
        except ProcessLookupError:
            pass
    except asyncio.CancelledError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise

    if proc.returncode not in (0, None) and not result.timed_out and result.success is False:
        stderr = b""
        if proc.stderr is not None:
            try:
                stderr = await asyncio.wait_for(proc.stderr.read(), timeout=5)
            except (TimeoutError, Exception):
                stderr = b""
        if stderr and not result.error:
            result.error = stderr.decode("utf-8", errors="replace")[:500]

    result.wall_ms = int((time.monotonic() - start) * 1000)
    return result


def _apply(result: AgentResult, event: ev.Event, start: float) -> None:
    if event.kind == ev.TOOL_USE:
        result.tool_calls += 1
        if event.tool_name in ("Edit", "Write", "NotebookEdit"):
            result.edits += 1
            if result.time_to_first_edit_ms is None:
                result.time_to_first_edit_ms = int((time.monotonic() - start) * 1000)
        elif event.tool_name == "Bash":
            result.bash_calls += 1
    elif event.kind == ev.RESULT:
        result.success = bool(event.success)
        result.duration_ms = event.duration_ms
        result.ttft_ms = event.ttft_ms
        result.num_turns = event.num_turns
        result.cost_usd = float(event.cost_usd or 0.0)
        if event.usage:
            for k in result.tokens:
                result.tokens[k] = int(event.usage.get(k, 0) or 0)
        if event.text:
            result.final_text = event.text


def run_agent_sync(
    cfg: Config,
    policy: ApprovalPolicy,
    name: str,
    prompt: str,
    cwd: Path,
    on_event: EventCallback | None = None,
    model: str | None = None,
) -> AgentResult:
    return asyncio.run(run_agent(cfg, policy, name, prompt, cwd, on_event, model))
