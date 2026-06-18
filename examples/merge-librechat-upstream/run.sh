#!/usr/bin/env bash
# Drive the LibreChat upstream merge with sidekick's self-hosted (local vLLM) agent.
#
# Pipeline:
#   1. fetch upstream (danny-avila) + the fork (arybach) remotes,
#   2. branch off the fork's main and attempt `git merge` of upstream main,
#   3. if it conflicts, commit the conflicted state as a real 2-parent merge commit so
#      sidekick's per-agent worktrees (created off HEAD) see the conflict markers,
#   4. run sidekick → local Qwen agent(s) resolve every marker, preserving the fork's
#      self-hosted config + search-aggregator subsystem, and validate (no markers remain),
#   5. you review and finalize/push (see FINALIZE at the end — sidekick never pushes).
#
# The model runs entirely on the local evo-x2 (Strix Halo) box. Start it first:
#   just -f scripts/serve_vllm.justfile serve-llamacpp   # or serve-vllm
#
# Usage:
#   examples/merge-librechat-upstream/run.sh
# Env overrides (defaults in brackets):
#   LIBRECHAT_DIR   [/mnt/backup/projects/LibreChat]  path to the fork checkout
#   UPSTREAM_REMOTE [origin]    remote tracking danny-avila/LibreChat
#   FORK_REMOTE     [myfork]    remote tracking arybach/LibreChat
#   UPSTREAM_BRANCH [main]
#   MERGE_BRANCH    [merge/upstream-YYYYMMDD]
set -euo pipefail

LIBRECHAT_DIR=${LIBRECHAT_DIR:-/mnt/backup/projects/LibreChat}
UPSTREAM_REMOTE=${UPSTREAM_REMOTE:-origin}
FORK_REMOTE=${FORK_REMOTE:-myfork}
UPSTREAM_BRANCH=${UPSTREAM_BRANCH:-main}
MERGE_BRANCH=${MERGE_BRANCH:-merge/upstream-$(date +%Y%m%d)}
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cd "$LIBRECHAT_DIR"
echo "==> fork: $LIBRECHAT_DIR  ($FORK_REMOTE ⇐ $UPSTREAM_REMOTE/$UPSTREAM_BRANCH)"

git fetch "$UPSTREAM_REMOTE" "$UPSTREAM_BRANCH"
git fetch "$FORK_REMOTE"

echo "==> branching $MERGE_BRANCH off $FORK_REMOTE/main"
git checkout -B "$MERGE_BRANCH" "$FORK_REMOTE/main"

echo "==> merging $UPSTREAM_REMOTE/$UPSTREAM_BRANCH"
set +e
git merge --no-edit "$UPSTREAM_REMOTE/$UPSTREAM_BRANCH"
merge_rc=$?
set -e

if [ "$merge_rc" -eq 0 ]; then
  echo "==> clean merge — no conflicts, no agent needed."
  echo "    review and: git push $FORK_REMOTE $MERGE_BRANCH"
  exit 0
fi

n_conflicts=$(git diff --name-only --diff-filter=U | wc -l | tr -d ' ')
echo "==> $n_conflicts conflicted path(s); committing the in-progress merge for the agents"
# `git add -A && git commit` while MERGE_HEAD is set creates a proper 2-parent merge commit,
# with the conflict markers as its content. sidekick worktrees branch off this commit.
git add -A
git commit --no-verify -m "WIP: merge $UPSTREAM_REMOTE/$UPSTREAM_BRANCH into $MERGE_BRANCH (markers unresolved)"

if ! command -v sidekick >/dev/null 2>&1; then
  echo "error: 'sidekick' not on PATH. Install once: uv tool install /mnt/backup/projects/sidekick" >&2
  exit 127
fi

echo "==> fanning out the local self-hosted agent to resolve conflicts"
sidekick --repo "$LIBRECHAT_DIR" run "$(cat "$HERE/TASK.md")" --json --no-vscode

cat <<EOF

==> resolution agents finished. Verify, then FINALIZE:
    cd "$LIBRECHAT_DIR"
    git diff --check                       # expect no output (no markers remain)
    grep -rIl -e '^<<<<<<<' -e '^>>>>>>>' --exclude-dir=node_modules --exclude-dir=.git . || true
    npm ci && npm run lint                  # or your full CI gate
    git push $FORK_REMOTE $MERGE_BRANCH      # then open a PR into $FORK_REMOTE/main
    # (optional) collapse the WIP merge + agent fix commits into one clean merge commit:
    #   git rebase -i $FORK_REMOTE/main      # squash the agent commits into the merge
EOF
