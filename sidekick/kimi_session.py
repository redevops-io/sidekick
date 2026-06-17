"""Native Kimi (Moonshot) agent backend — OpenAI-compatible `/v1` tool loop.

Implements a self-contained agentic coding loop (Raschka's components, applied directly)
against Moonshot's OpenAI-compatible chat-completions API with function calling. It is a
drop-in alternative to the Claude Code headless backend: same `AgentResult`, same
`on_event` stream for the dashboard, same auto-approval policy — so worktrees, metrics,
merge, and the live progress doc all work unchanged.

Tools exposed to the model: read_file, write_file, edit_file, list_dir, run_bash (gated by
the approval policy), and finish. All file/bash actions are confined to the agent's
worktree (cwd) and auto-approved per the policy — no human prompts.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import events as ev
from .agent_session import AgentResult
from .approval import ApprovalPolicy
from .config import DEFAULT_BASH_ALLOWLIST, Config
from .context_budget import clip


class KimiError(RuntimeError):
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
        raise KimiError(f"path '{rel}' escapes the working directory")
    return p


def _exec_tool(name: str, args: dict, cwd: Path, policy: ApprovalPolicy) -> str:
    """Execute a tool, returning a string result. Errors become messages for the model
    (a bad path or missing arg must not crash the agent loop)."""
    try:
        return _dispatch_tool(name, args, cwd, policy)
    except (KimiError, OSError, KeyError) as e:
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
        try:
            proc = subprocess.run(cmd, cwd=str(cwd), shell=True, capture_output=True, text=True, timeout=300)
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


# --- HTTP --------------------------------------------------------------------

def _chat(cfg: Config, messages: list[dict], tools: list[dict] | None, timeout: int) -> dict:
    # kimi-k2.x reasoning models require temperature == 1 (others accept it fine).
    body: dict = {"model": cfg.openai_model, "messages": messages, "temperature": 1}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    req = urllib.request.Request(
        f"{cfg.openai_base_url.rstrip('/')}/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {cfg.openai_api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise KimiError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:300]}") from e
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
        raise KimiError(f"request failed: {e}") from e


def _accum_usage(result: AgentResult, usage: dict | None) -> None:
    if not usage:
        return
    result.tokens["input"] += int(usage.get("prompt_tokens", 0) or 0)
    result.tokens["output"] += int(usage.get("completion_tokens", 0) or 0)
    details = usage.get("prompt_tokens_details") or {}
    result.tokens["cache_read"] += int(details.get("cached_tokens", usage.get("cached_tokens", 0)) or 0)


# --- the agent loop ----------------------------------------------------------

def _run_kimi_sync(cfg, policy, name, prompt, cwd, on_event, append_system) -> AgentResult:
    result = AgentResult(name=name)
    start = time.monotonic()
    cwd = Path(cwd).resolve()
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
            data = _chat(cfg, messages, tools, req_timeout)
        except KimiError as e:
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
            except json.JSONDecodeError:
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


def kimi_complete(cfg: Config, system: str, user: str, timeout: int = 180) -> str:
    """One-shot completion (no tools) — used for task planning on the kimi branch."""
    if not cfg.openai_api_key:
        raise KimiError("no Kimi API key (set KIMI_AGENT_API_KEY)")
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    data = _chat(cfg, messages, None, timeout)
    return ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""


async def run_kimi_agent(cfg, policy, name, prompt, cwd, on_event=None, model=None, append_system=None) -> AgentResult:
    """Async wrapper mirroring agent_session.run_agent so the orchestrator can dispatch."""
    if not cfg.openai_api_key:
        r = AgentResult(name=name)
        r.error = "no Kimi API key (set KIMI_AGENT_API_KEY)"
        return r
    return await asyncio.to_thread(_run_kimi_sync, cfg, policy, name, prompt, cwd, on_event, append_system)
