"""Git worktree backend (Hermes-style pluggable execution backend).

Each agent runs in its own worktree + branch so parallel agents never collide on the
working tree. This isolates file changes (objective A3: low merge-conflict rate) and lets
loopie merge green branches selectively.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path


class GitError(RuntimeError):
    pass


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip() or proc.stdout.strip()}")
    return proc.stdout.strip()


def ensure_repo(root: Path) -> None:
    """Ensure root is a git repo with at least one commit (worktrees need a base ref)."""
    if not (root / ".git").exists():
        _git(["init"], root)
    # Need at least one commit for `git worktree add -b ... <base>`.
    has_head = subprocess.run(
        ["git", "rev-parse", "--verify", "HEAD"], cwd=root, capture_output=True, text=True
    ).returncode == 0
    if not has_head:
        # Seed a base commit. Ship a .gitignore for Python bytecode + loopie state so
        # acceptance checks (which generate __pycache__/*.pyc) never pollute branches or
        # block merges with "untracked files would be overwritten".
        gi = root / ".gitignore"
        if not gi.exists():
            gi.write_text("__pycache__/\n*.pyc\n.loopie/\n", encoding="utf-8")
            _git(["add", ".gitignore"], root)
            _git(["commit", "-m", "loopie: base commit"], root)
        else:
            _git(["commit", "--allow-empty", "-m", "loopie: base commit"], root)


@dataclass
class Worktree:
    path: Path
    branch: str
    base: str
    root: Path

    def remove(self, force: bool = True) -> None:
        args = ["worktree", "remove"]
        if force:
            args.append("--force")
        args.append(str(self.path))
        try:
            _git(args, self.root)
        except GitError:
            pass
        # Best-effort branch cleanup.
        try:
            _git(["branch", "-D", self.branch], self.root)
        except GitError:
            pass


class WorktreeManager:
    def __init__(self, root: Path, worktrees_dir: Path, base_ref: str = "HEAD"):
        self.root = Path(root).resolve()
        self.worktrees_dir = Path(worktrees_dir).resolve()
        self.worktrees_dir.mkdir(parents=True, exist_ok=True)
        ensure_repo(self.root)
        self._base_ref = base_ref
        # Resolve base to a stable commit sha so every worktree branches from the same point.
        self.base = _git(["rev-parse", base_ref], self.root)

    def refresh_base(self) -> str:
        """Re-resolve the base to current HEAD so later waves see merged dependencies."""
        self.base = _git(["rev-parse", "HEAD"], self.root)
        return self.base

    def create(self, name: str) -> Worktree:
        branch = f"loopie/{name}"
        path = self.worktrees_dir / name
        if path.exists():
            # Stale leftover from a prior run.
            Worktree(path=path, branch=branch, base=self.base, root=self.root).remove()
        _git(["worktree", "add", "-b", branch, str(path), self.base], self.root)
        return Worktree(path=path, branch=branch, base=self.base, root=self.root)

    def has_changes(self, wt: Worktree) -> bool:
        status = _git(["status", "--porcelain"], wt.path)
        return bool(status.strip())

    def commit_all(self, wt: Worktree, message: str) -> bool:
        """Stage and commit everything in the worktree. Returns True if a commit was made."""
        if not self.has_changes(wt):
            return False
        _git(["add", "-A"], wt.path)
        # Defensively unstage Python bytecode so it never enters a branch or blocks a
        # merge, even in repos that lack loopie's .gitignore.
        subprocess.run(
            ["git", "reset", "-q", "--", "*.pyc", ":(glob)**/__pycache__/**"],
            cwd=str(wt.path),
            capture_output=True,
            text=True,
        )
        staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=str(wt.path)
        ).returncode != 0
        if not staged:  # only bytecode changed — nothing real to commit
            return False
        _git(["commit", "-m", message], wt.path)
        return True

    def merge_clean(self, wt: Worktree, into: str | None = None) -> bool:
        """Attempt to merge a worktree branch into `into` (default: base branch).

        Returns True on a clean merge; aborts and returns False on conflict (A3 metric).
        """
        target = into or _current_branch(self.root)
        try:
            _git(["merge", "--no-edit", wt.branch], self.root)
            return True
        except GitError:
            try:
                _git(["merge", "--abort"], self.root)
            except GitError:
                pass
            _ = target
            return False


def _current_branch(root: Path) -> str:
    try:
        return _git(["rev-parse", "--abbrev-ref", "HEAD"], root)
    except GitError:
        return "HEAD"
