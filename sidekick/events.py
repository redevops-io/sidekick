"""Normalize Claude Code `stream-json` lines into typed sidekick events.

Schema verified against a live `claude -p --output-format stream-json` run:
  * system/init    -> session_id, cwd, model, tools, permissionMode
  * assistant      -> message.content[] of {text} and {tool_use} blocks; per-msg usage
  * user           -> message.content[] of {tool_result} blocks
  * rate_limit_event
  * result         -> duration_ms, ttft_ms, num_turns, total_cost_usd, usage{...}
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# Event kinds.
INIT = "init"
TEXT = "text"
TOOL_USE = "tool_use"
TOOL_RESULT = "tool_result"
RESULT = "result"
RATE_LIMIT = "rate_limit"
ERROR = "error"
RAW = "raw"


@dataclass
class Event:
    kind: str
    raw: dict[str, Any] = field(default_factory=dict)
    # Common extracted fields (populated by kind).
    session_id: str | None = None
    text: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None
    tool_use_id: str | None = None
    tool_result: str | None = None
    model: str | None = None
    # Result-only.
    success: bool | None = None
    duration_ms: int | None = None
    ttft_ms: int | None = None
    num_turns: int | None = None
    cost_usd: float | None = None
    usage: dict[str, Any] | None = None


def _usage_tokens(usage: dict[str, Any]) -> dict[str, int]:
    """Flatten the usage block into simple token counts."""
    return {
        "input": int(usage.get("input_tokens", 0) or 0),
        "output": int(usage.get("output_tokens", 0) or 0),
        "cache_read": int(usage.get("cache_read_input_tokens", 0) or 0),
        "cache_creation": int(usage.get("cache_creation_input_tokens", 0) or 0),
    }


def parse_line(line: str) -> list[Event]:
    """Parse one NDJSON line into zero or more normalized Events.

    A single assistant message may carry several content blocks (text + multiple
    tool_use), so this returns a list. Non-JSON lines become a single RAW event.
    """
    line = line.strip()
    if not line:
        return []
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return [Event(kind=RAW, text=line)]

    t = obj.get("type")
    sid = obj.get("session_id")

    if t == "system" and obj.get("subtype") == "init":
        return [Event(kind=INIT, raw=obj, session_id=sid, model=obj.get("model"))]

    if t == "assistant":
        out: list[Event] = []
        msg = obj.get("message", {})
        model = msg.get("model")
        for block in msg.get("content", []) or []:
            bt = block.get("type")
            if bt == "text" and block.get("text"):
                out.append(Event(kind=TEXT, raw=obj, session_id=sid, model=model, text=block["text"]))
            elif bt == "tool_use":
                out.append(
                    Event(
                        kind=TOOL_USE,
                        raw=obj,
                        session_id=sid,
                        model=model,
                        tool_name=block.get("name"),
                        tool_input=block.get("input") or {},
                        tool_use_id=block.get("id"),
                    )
                )
        return out

    if t == "user":
        out = []
        for block in obj.get("message", {}).get("content", []) or []:
            if block.get("type") == "tool_result":
                content = block.get("content")
                if isinstance(content, list):
                    content = " ".join(
                        c.get("text", "") for c in content if isinstance(c, dict)
                    )
                out.append(
                    Event(
                        kind=TOOL_RESULT,
                        raw=obj,
                        session_id=sid,
                        tool_use_id=block.get("tool_use_id"),
                        tool_result=str(content) if content is not None else "",
                    )
                )
        return out

    if t == "rate_limit_event":
        return [Event(kind=RATE_LIMIT, raw=obj, session_id=sid)]

    if t == "result":
        usage = obj.get("usage", {}) or {}
        return [
            Event(
                kind=RESULT,
                raw=obj,
                session_id=sid,
                success=(obj.get("subtype") == "success" and not obj.get("is_error")),
                duration_ms=obj.get("duration_ms"),
                ttft_ms=obj.get("ttft_ms"),
                num_turns=obj.get("num_turns"),
                cost_usd=obj.get("total_cost_usd"),
                usage=_usage_tokens(usage),
                text=obj.get("result"),
            )
        ]

    return [Event(kind=RAW, raw=obj)]
