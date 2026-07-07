"""Task decomposition planner.

Calls Claude Code headless (read-only, single-shot JSON) to decompose a high-level task
into a DAG of independent subtasks with acceptance checks. Falls back to a single
whole-task subtask if planning fails, so a run is always possible.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .config import Config
from .prompts import PLANNER_SYSTEM, planner_prompt
from .repo_context import RepoContext


@dataclass
class Subtask:
    id: str
    title: str
    description: str
    target_files: list[str] = field(default_factory=list)
    deps: list[str] = field(default_factory=list)
    acceptance_checks: list[str] = field(default_factory=list)
    # Background subagent (ported from Hermes 0.17): launched up-front and run
    # asynchronously alongside the foreground dependency waves, then joined + merged last.
    # Default False preserves the wave-barrier scheduling.
    background: bool = False

    def as_block(self) -> str:
        parts = [f"Title: {self.title}", f"Description: {self.description}"]
        if self.target_files:
            parts.append("Target files: " + ", ".join(self.target_files))
        if self.acceptance_checks:
            parts.append("Acceptance checks (must pass):\n  - " + "\n  - ".join(self.acceptance_checks))
        return "\n".join(parts)


@dataclass
class Plan:
    task: str
    subtasks: list[Subtask]

    def to_dict(self) -> dict:
        return {
            "task": self.task,
            "subtasks": [
                {
                    "id": s.id,
                    "title": s.title,
                    "description": s.description,
                    "target_files": s.target_files,
                    "deps": s.deps,
                    "acceptance_checks": s.acceptance_checks,
                    "background": s.background,
                }
                for s in self.subtasks
            ],
        }


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    # Strip ```json fences if present.
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    # Otherwise grab the outermost {...}.
    if not text.startswith("{"):
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            text = m.group(0)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _parse_plan(task: str, data: dict) -> Plan:
    subs = []
    for raw in data.get("subtasks", []):
        if not raw.get("id") or not raw.get("description"):
            continue
        subs.append(
            Subtask(
                id=str(raw["id"]).strip(),
                title=str(raw.get("title", raw["id"])).strip(),
                description=str(raw["description"]).strip(),
                target_files=[str(f) for f in raw.get("target_files", [])],
                deps=[str(d) for d in raw.get("deps", [])],
                acceptance_checks=[str(c) for c in raw.get("acceptance_checks", [])],
                background=bool(raw.get("background", False)),
            )
        )
    # Drop deps that reference unknown ids.
    ids = {s.id for s in subs}
    for s in subs:
        s.deps = [d for d in s.deps if d in ids and d != s.id]
    if not subs:
        return _fallback_plan(task)
    return Plan(task=task, subtasks=subs)


def _fallback_plan(task: str) -> Plan:
    return Plan(
        task=task,
        subtasks=[
            Subtask(
                id="main",
                title="Complete task",
                description=task,
                acceptance_checks=[],
            )
        ],
    )


def make_plan(cfg: Config, ctx: RepoContext, task: str, max_subtasks: int = 6) -> Plan:
    """Produce a Plan by querying the configured provider; fall back to a single subtask."""
    prompt = planner_prompt(task, ctx.render(), max_subtasks)

    from .providers import is_claude

    if not is_claude(getattr(cfg, "provider", "claude")):
        from .llm_session import LLMError, llm_complete

        try:
            text = llm_complete(cfg, PLANNER_SYSTEM, prompt)
        except LLMError:
            return _fallback_plan(task)
        data = _extract_json(text)
        return _parse_plan(task, data) if data else _fallback_plan(task)

    cmd = [
        cfg.claude_bin,
        "-p",
        prompt,
        "--output-format",
        "json",
        "--append-system-prompt",
        PLANNER_SYSTEM,
        "--permission-mode",
        "acceptEdits",
        "--allowedTools",
        "Read Grep Glob",
        "--max-turns",
        "6",
    ]
    if cfg.planner_model:
        cmd += ["--model", cfg.planner_model]
    try:
        proc = subprocess.run(
            cmd, cwd=str(cfg.repo_root), capture_output=True, text=True, timeout=300
        )
    except (subprocess.SubprocessError, OSError):
        return _fallback_plan(task)
    if proc.returncode != 0:
        return _fallback_plan(task)
    # --output-format json wraps the reply: {"type":"result","result":"...text..."}.
    text = proc.stdout.strip()
    outer = _extract_json(text)
    inner_text = ""
    if outer and isinstance(outer.get("result"), str):
        inner_text = outer["result"]
    else:
        inner_text = text
    data = _extract_json(inner_text)
    if not data:
        return _fallback_plan(task)
    return _parse_plan(task, data)


def save_plan(plan: Plan, path: Path) -> None:
    path.write_text(json.dumps(plan.to_dict(), indent=2), encoding="utf-8")


def load_plan(path: Path) -> Plan:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return _parse_plan(data.get("task", ""), data)
