"""Live repo context (Raschka #1).

Gathers stable facts about the repository up front — branch, dirty state, project tree,
and documentation excerpts — so agents and the planner never operate blindly on vague
instructions like "fix the tests".
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .context_budget import clip

_DOC_CANDIDATES = ("README.md", "CLAUDE.md", "AGENTS.md", "CONTRIBUTING.md", "Justfile")
_SKIP_DIRS = {".git", ".sidekick", "__pycache__", ".venv", "node_modules", ".pytest_cache", ".ruff_cache"}


def _run(args: list[str], cwd: Path) -> str:
    try:
        return subprocess.run(
            args, cwd=cwd, capture_output=True, text=True, timeout=15
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return ""


@dataclass
class RepoContext:
    root: Path
    branch: str = ""
    dirty: list[str] = field(default_factory=list)
    tree: str = ""
    docs: dict[str, str] = field(default_factory=dict)
    is_git: bool = False

    def render(self, doc_budget: int = 1500) -> str:
        """Render a compact workspace summary suitable for a prompt."""
        parts = [f"# Workspace: {self.root.name}", f"Path: {self.root}"]
        if self.is_git:
            parts.append(f"Git branch: {self.branch or '(detached/unknown)'}")
            if self.dirty:
                shown = ", ".join(self.dirty[:20])
                more = "" if len(self.dirty) <= 20 else f" (+{len(self.dirty) - 20} more)"
                parts.append(f"Uncommitted changes: {shown}{more}")
            else:
                parts.append("Working tree: clean")
        else:
            parts.append("Git: not a repository")
        parts.append("\n## Project layout\n" + (self.tree or "(empty)"))
        for name, body in self.docs.items():
            parts.append(f"\n## {name}\n{clip(body, doc_budget)}")
        return "\n".join(parts)


def _build_tree(root: Path, max_entries: int = 200) -> str:
    lines: list[str] = []
    count = 0
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in _SKIP_DIRS for part in rel.parts):
            continue
        depth = len(rel.parts) - 1
        if depth > 3:
            continue
        indent = "  " * depth
        suffix = "/" if path.is_dir() else ""
        lines.append(f"{indent}{rel.name}{suffix}")
        count += 1
        if count >= max_entries:
            lines.append("  ... (truncated)")
            break
    return "\n".join(lines)


def gather(root: Path) -> RepoContext:
    root = Path(root).resolve()
    is_git = (root / ".git").exists()
    branch = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], root) if is_git else ""
    dirty: list[str] = []
    if is_git:
        status = _run(["git", "status", "--porcelain"], root)
        dirty = [ln[3:].strip() for ln in status.splitlines() if ln.strip()]
    docs: dict[str, str] = {}
    for name in _DOC_CANDIDATES:
        p = root / name
        if p.is_file():
            try:
                docs[name] = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                pass
    return RepoContext(
        root=root, branch=branch, dirty=dirty, tree=_build_tree(root), docs=docs, is_git=is_git
    )
