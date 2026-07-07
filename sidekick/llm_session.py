"""Universal agent backend — a self-contained agentic coding loop over **LiteLLM**.

Drop-in alternative to the native Claude Code headless backend for *every* non-Claude
provider. The loop (read_file / write_file / edit_file / list_dir / run_bash / finish, all
sandboxed to the agent's worktree and auto-approved per the policy) is provider-agnostic —
the only provider-specific step is the chat completion, which goes through
`litellm.completion()`. That single indirection is what collapses the old per-provider
`kimi_session.py` variants (kimi / openai / gemini / grok / vLLM) into one file: the
provider is just a LiteLLM model string (see providers.py).

Same `AgentResult`, same `on_event` stream for the dashboard, same auto-approval policy —
so worktrees, metrics, merge, and the live progress doc all work unchanged.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path

from . import events as ev
from .agent_session import AgentResult
from .approval import ApprovalPolicy
from .config import DEFAULT_BASH_ALLOWLIST, Config
from .context_budget import clip
from .providers import LLMSettings


class LLMError(RuntimeError):
    pass


# --- tool schema -------------------------------------------------------------

def _tool_specs(policy: ApprovalPolicy) -> list[dict]:
    specs = [
        _fn("read_file", "Read a UTF-8 text file relative to the working directory.",
            {"path": {"type": "string"}}, ["path"]),
        _fn("write_file", "Create or overwrite a file (relative path) with exact content.",
            {"path": {"type": "string"}, "content": {"type": "string"}}, ["path", "content"]),
        _fn("edit_file", "Replace the first occurrence of `old` with `new` in a file.",
            {"path": {"type": "string"}, "old": {"type": "string"}, "new": {"type": "string"}},
            ["path", "old", "new"]),
        _fn("list_dir", "List files under a directory (relative path; default '.').",
            {"path": {"type": "string"}}, []),
        _fn("finish", "Call when the subtask is complete and acceptance checks should pass.",
            {"summary": {"type": "string"}}, []),
    ]
    if policy.can_run_bash:
        specs.append(
            _fn("run_bash", "Run a shell command in the working directory (build/test/lint/vcs).",
                {"command": {"type": "string"}}, ["command"])
        )
    return specs


def _fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


def _bash_prefixes() -> list[str]:
    """Allowed command prefixes derived from DEFAULT_BASH_ALLOWLIST (e.g. 'Bash(uv *)' -> 'uv ')."""
    out = []
    for pat in DEFAULT_BASH_ALLOWLIST:
        inner = pat[pat.find("(") + 1 : pat.rfind(")")]
        out.append(inner[:-1] if inner.endswith("*") else inner)
    return out


# --- tool execution (sandboxed to cwd, auto-approved per policy) --------------

def _safe_path(cwd: Path, rel: str) -> Path:
    p = (cwd / rel).resolve()
    if cwd not in p.parents and p != cwd:
        raise LLMError(f"path '{rel}' escapes the working directory")
    return p


def _exec_tool(name: str, args: dict, cwd: Path, policy: ApprovalPolicy) -> str:
    """Execute a tool, returning a string result. Errors become messages for the model
    (a bad path or missing arg must not crash the agent loop)."""
    try:
        return _dispatch_tool(name, args, cwd, policy)
    except (LLMError, OSError, KeyError) as e:
        return f"error: {e}"


def _dispatch_tool(name: str, args: dict, cwd: Path, policy: ApprovalPolicy) -> str:
    if name == "read_file":
        p = _safe_path(cwd, args["path"])
        if not p.is_file():
            return f"error: no such file '{args['path']}'"
        return clip(p.read_text(encoding="utf-8", errors="replace"), 8000)
    if name == "write_file":
        p = _safe_path(cwd, args["path"])
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(args.get("content", ""), encoding="utf-8")
        return f"wrote {args['path']} ({len(args.get('content', ''))} bytes)"
    if name == "edit_file":
        p = _safe_path(cwd, args["path"])
        if not p.is_file():
            return f"error: no such file '{args['path']}'"
        text = p.read_text(encoding="utf-8", errors="replace")
        old = args.get("old", "")
        if old not in text:
            return "error: `old` text not found; read the file and retry"
        p.write_text(text.replace(old, args.get("new", ""), 1), encoding="utf-8")
        return f"edited {args['path']}"
    if name == "list_dir":
        p = _safe_path(cwd, args.get("path", "."))
        if not p.is_dir():
            return f"error: no such directory '{args.get('path', '.')}'"
        return "\n".join(sorted(c.name + ("/" if c.is_dir() else "") for c in p.iterdir())) or "(empty)"
    if name == "run_bash":
        cmd = args.get("command", "")
        if not policy.can_run_bash:
            return "error: bash is disabled by the approval policy"
        if not policy.requires_dangerous_flag:  # allowlist mode → enforce prefixes
            if not any(cmd.strip().startswith(pre) for pre in _bash_prefixes()):
                return f"error: command not on the auto-approve allowlist: {clip(cmd, 80)}"
        # Build/install commands (npm ci, turbo build, large test suites) legitimately run
        # for many minutes; cap generously and allow override via SIDEKICK_BASH_TIMEOUT.
        bash_timeout = int(os.environ.get("SIDEKICK_BASH_TIMEOUT", "1800"))
        try:
            proc = subprocess.run(
                cmd, cwd=str(cwd), shell=True, capture_output=True, text=True, timeout=bash_timeout
            )
        except (OSError, subprocess.SubprocessError) as e:
            return f"error running command: {e}"
        return clip(f"[exit {proc.returncode}]\n{proc.stdout}{proc.stderr}", 4000)
    return f"error: unknown tool '{name}'"


# canonical names for the dashboard (so edits are counted, actions render nicely)
_CANON = {
    "write_file": ("Write", "file_path", "path"),
    "edit_file": ("Edit", "file_path", "path"),
    "read_file": ("Read", "file_path", "path"),
    "list_dir": ("Glob", "pattern", "path"),
    "run_bash": ("Bash", "command", "command"),
    "finish": ("Finish", None, None),
}


# --- LiteLLM completion ------------------------------------------------------

def _to_dict(resp) -> dict:
    """Normalize a LiteLLM ModelResponse to an OpenAI-style dict the loop consumes."""
    for attr in ("model_dump", "dict"):
        fn = getattr(resp, attr, None)
        if callable(fn):
            try:
                return fn()
            except Exception:  # noqa: BLE001 — fall through to the next strategy
                pass
    if isinstance(resp, dict):
        return resp
    return json.loads(getattr(resp, "json", lambda: "{}")())


def _completion(settings: LLMSettings, messages: list[dict], tools: list[dict] | None, timeout: int) -> dict:
    """One `litellm.completion` call. Any provider/model — settings carry model, api_base,
    api_key, temperature. Raises LLMError on any failure (kept out of the hot import path)."""
    import litellm

    kwargs: dict = {"model": settings.model, "messages": messages, "timeout": timeout}
    if settings.api_base:
        kwargs["api_base"] = settings.api_base
    if settings.api_key:
        kwargs["api_key"] = settings.api_key
    if settings.temperature is not None:
        kwargs["temperature"] = settings.temperature
    if tools:
        kwargs["tools"] = tools
        kwargs["tool_choice"] = "auto"
    try:
        resp = litellm.completion(**kwargs)
    except Exception as e:  # noqa: BLE001 — LiteLLM raises many provider-specific types
        raise LLMError(f"{type(e).__name__}: {clip(str(e), 300)}") from e
    return _to_dict(resp)


def _accum_usage(result: AgentResult, usage: dict | None) -> None:
    if not usage:
        return
    result.tokens["input"] += int(usage.get("prompt_tokens", 0) or 0)
    result.tokens["output"] += int(usage.get("completion_tokens", 0) or 0)
    details = usage.get("prompt_tokens_details") or {}
    result.tokens["cache_read"] += int(details.get("cached_tokens", usage.get("cached_tokens", 0)) or 0)


# --- the agent loop ----------------------------------------------------------

def _run_llm_sync(cfg, policy, name, prompt, cwd, on_event, append_system) -> AgentResult:
    result = AgentResult(name=name)
    start = time.monotonic()
    cwd = Path(cwd).resolve()
    settings = cfg.llm()
    tools = _tool_specs(policy)
    req_timeout = min(cfg.agent_timeout_s, 240)

    messages: list[dict] = []
    if append_system:
        messages.append({"role": "system", "content": append_system})
    messages.append({"role": "user", "content": prompt})

    def emit(event: ev.Event) -> None:
        if on_event is not None:
            on_event(name, event)

    finished = False
    turns = 0
    while turns < cfg.agent_max_turns:
        turns += 1
        try:
            data = _completion(settings, messages, tools, req_timeout)
        except LLMError as e:
            result.error = str(e)
            break
        _accum_usage(result, data.get("usage"))
        msg = (data.get("choices") or [{}])[0].get("message") or {}
        content = msg.get("content")
        tool_calls = msg.get("tool_calls") or []
        if content:
            emit(ev.Event(kind=ev.TEXT, text=content))
            result.final_text = content
        messages.append(_assistant_message(msg, content, tool_calls))

        if not tool_calls:
            finished = True  # model produced a final answer with no further actions
            break

        for tc in tool_calls:
            fn = (tc.get("function") or {}).get("name", "")
            try:
                args = json.loads((tc.get("function") or {}).get("arguments") or "{}")
            except (json.JSONDecodeError, TypeError):
                args = {}
            result.tool_calls += 1
            canon, in_key, src_key = _CANON.get(fn, (fn, None, None))
            tool_input = {in_key: args.get(src_key, "")} if in_key else {}
            emit(ev.Event(kind=ev.TOOL_USE, tool_name=canon, tool_input=tool_input))
            if fn in ("write_file", "edit_file"):
                result.edits += 1
                if result.time_to_first_edit_ms is None:
                    result.time_to_first_edit_ms = int((time.monotonic() - start) * 1000)
            elif fn == "run_bash":
                result.bash_calls += 1
            if fn == "finish":
                finished = True
                out = "ok"
            else:
                out = _exec_tool(fn, args, cwd, policy)
            messages.append({"role": "tool", "tool_call_id": tc.get("id"), "content": clip(out, cfg.clip_tool_output)})
        if finished:
            break

    result.num_turns = turns
    result.success = finished and not result.error
    result.wall_ms = int((time.monotonic() - start) * 1000)
    emit(ev.Event(kind=ev.RESULT, success=result.success, num_turns=turns, usage=dict(result.tokens)))
    return result


def _assistant_message(msg: dict, content, tool_calls) -> dict:
    out: dict = {"role": "assistant", "content": content or ""}
    if tool_calls:
        out["tool_calls"] = tool_calls
    return out


def llm_complete(cfg: Config, system: str, user: str, timeout: int = 180) -> str:
    """One-shot completion (no tools) — used for task planning on non-Claude providers."""
    settings = cfg.llm()
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    data = _completion(settings, messages, None, timeout)
    return ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""


async def run_llm_agent(cfg, policy, name, prompt, cwd, on_event=None, model=None, append_system=None) -> AgentResult:
    """Async wrapper mirroring agent_session.run_agent so the orchestrator can dispatch."""
    return await asyncio.to_thread(_run_llm_sync, cfg, policy, name, prompt, cwd, on_event, append_system)
