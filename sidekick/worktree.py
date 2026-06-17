"""Git worktree backend (Hermes-style pluggable execution backend).

Each agent runs in its own worktree + branch so parallel agents never collide on the
working tree. This isolates file changes (objective A3: low merge-conflict rate) and lets
sidekick merge green branches selectively.
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
        # Seed a base commit. Ship a .gitignore for Python bytecode + sidekick state so
        # acceptance checks (which generate __pycache__/*.pyc) never pollute branches or
        # block merges with "untracked files would be overwritten".
        gi = root / ".gitignore"
        if not gi.exists():
            gi.write_text("__pycache__/\n*.pyc\n.sidekick/\n", encoding="utf-8")
            _git(["add", ".gitignore"], root)
            _git(["commit", "-m", "sidekick: base commit"], root)
        else:
            _git(["commit", "--allow-empty", "-m", "sidekick: base commit"], root)


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
        branch = f"sidekick/{name}"
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
        """Stage and commit everything in the worktree.

        Returns True if a commit was made.

        Defends against agent **branch drift**: a subtask whose task
        requires touching git (e.g. "rebase open PR #22") may run
        `git checkout -b pr-22-rebase` inside the worktree to do its
        work. HEAD silently moves off `wt.branch`; any commit we then
        make (and any subsequent merge of `wt.branch`) loses the
        work because the agent's commits + uncommitted artifacts are
        all sitting on a branch sidekick doesn't know about.

        Before staging, we re-anchor `wt.branch` to whatever HEAD is
        right now and check it out. This is a no-op when the agent
        stayed on its assigned branch, and a one-shot rescue when it
        didn't:

          * commits the agent made on a side branch get absorbed
            into `wt.branch` (they're already in the ref's history
            via the SHA we anchor to).
          * untracked / uncommitted artifacts in the working tree
            stay put — the next `git add -A` picks them up.
          * the eventual `merge_clean(wt)` then carries everything
            up to the orchestrator's main branch.
        """
        current_branch = _git(["rev-parse", "--abbrev-ref", "HEAD"], wt.path).strip()
        if current_branch != wt.branch:
            # Anchor wt.branch to the agent's current HEAD SHA and
            # check it out. Uncommitted working-tree changes carry
            # over so the next `git add -A` picks them up.
            current_sha = _git(["rev-parse", "HEAD"], wt.path).strip()
            _git(["checkout", "-B", wt.branch, current_sha], wt.path)

        if not self.has_changes(wt):
            return False
        _git(["add", "-A"], wt.path)
        # Defensively unstage Python bytecode so it never enters a branch or blocks a
        # merge, even in repos that lack sidekick's .gitignore.
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
