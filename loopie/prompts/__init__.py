"""Cache-shaped prompt construction (Raschka #2).

Prompts are split into a STABLE PREFIX (instructions + workspace summary) that rarely
changes across a run — maximizing Claude Code's prompt-cache reuse (objective E2) — and a
DYNAMIC SUFFIX (the specific subtask) that varies per agent.
"""

from __future__ import annotations

# Stable system-prompt fragment appended to every spawned agent. Kept identical across
# agents in a run so the cached prefix is reused (E2: cache-hit ratio).
AGENT_SYSTEM_PREFIX = """\
You are a loopie worker agent: an autonomous coding agent operating on an isolated git
worktree/branch as part of a larger orchestrated task. Principles:
- CRITICAL: operate ONLY inside your current working directory. Create and edit files
  using paths relative to it. Never use absolute paths and never write outside it — your
  worktree is your sandbox and other agents work in parallel elsewhere.
- "The repo root" always means your current working directory, not any path mentioned in
  the workspace summary.
- Make the smallest correct change that satisfies your subtask's acceptance checks.
- Stay within your subtask's stated target files; do not refactor unrelated code.
- After editing, run the acceptance checks yourself when you have a shell, and fix
  failures before finishing.
- Be decisive and terminal: finish the subtask without asking questions; you are fully
  auto-approved and there is no human to answer.
- Prefer existing project conventions (linters, test layout, style) over inventing new ones.
"""

PLANNER_SYSTEM = """\
You are loopie's planner. Decompose a high-level coding task into a minimal DAG of
independent subtasks that can run in parallel on separate git branches. Maximize
parallelism while minimizing cross-subtask file overlap (to avoid merge conflicts).
Output STRICT JSON only — no prose, no code fences."""


def agent_prompt(subtask_block: str, workspace_summary: str, cwd: str | None = None) -> str:
    """Compose a worker prompt: stable workspace prefix + dynamic subtask suffix."""
    cwd_line = (
        f"\nYour working directory is `{cwd}`. Create/edit files relative to it; do not "
        "write anywhere else.\n"
        if cwd
        else ""
    )
    return (
        f"{workspace_summary}\n{cwd_line}\n"
        f"---\n# Your subtask\n{subtask_block}\n\n"
        "Complete this subtask now, writing only inside your working directory. "
        "When done, ensure the acceptance checks pass."
    )


def planner_prompt(task: str, workspace_summary: str, max_subtasks: int) -> str:
    schema = """{
  "subtasks": [
    {
      "id": "kebab-case-id",
      "title": "short title",
      "description": "what to do, concretely, including which files to create/edit",
      "target_files": ["relative/path.py"],
      "deps": ["other-subtask-id"],
      "acceptance_checks": ["shell command that must exit 0, e.g. 'ruff check .' or 'pytest tests/test_x.py'"]
    }
  ]
}"""
    return (
        f"{workspace_summary}\n\n"
        f"---\n# Task to decompose\n{task}\n\n"
        f"Produce at most {max_subtasks} subtasks. Each subtask must be independently "
        "completable by a separate agent. Put a dependency in `deps` only when one "
        "subtask genuinely needs another's output. Keep `target_files` disjoint across "
        "subtasks where possible. acceptance_checks are shell commands run from the repo "
        "root; use checks that actually exist or are trivial to add.\n\n"
        f"Output JSON matching exactly this schema:\n{schema}"
    )
