---
name: "delegate-to-sidekick"
description: "Delegate a multi-step coding task to sidekick — a local orchestrator that decomposes the task into a DAG of subtasks, fans out auto-approved coding sub-sessions on isolated git worktrees (Claude Code on the `claude` branch, Kimi/Moonshot on the `kimi` branch — same envelope either way), runs acceptance checks, and merges the green branches. Use when the user says 'delegate to sidekick', 'run this in parallel via sidekick', 'fan this out', 'hand this off and report back', or when a task is clearly multi-file / parallelizable and you want it driven by a separate, isolated set of sub-sessions instead of the current one."
license: "Apache-2.0"
version: "0.1.0"
---

# delegate-to-sidekick

`sidekick` is a separate orchestrator process (CLI on PATH at `~/.local/bin/sidekick`, source at `/mnt/backup/projects/sidekick`) that runs multi-step coding tasks **outside** the current Claude Code session. It plans the work, spawns one isolated sub-session per subtask on its own git worktree+branch, runs acceptance checks, and merges the green branches back into the source repo. You invoke it via a single CLI call and receive a structured JSON envelope of the result.

The local install (`uv tool install`) determines which sub-agent runtime the spawned sessions use:

- **claude** branch: spawns headless `claude -p …` Claude Code sub-sessions.
- **kimi** branch: spawns Kimi (Moonshot) `/v1` agentic loops by default, with a `--provider claude` fallback per-run.

The envelope shape is identical across both branches; the `backend` field (on the kimi branch) tells you which runtime was actually used.

## When to use

- The user explicitly says "delegate to sidekick", "fan this out", "run this in parallel", "hand it off", or similar.
- The task is naturally decomposable into 2+ semi-independent subtasks (e.g. "add a CLI flag + write the docs + add a unit test", "refactor module A + update its callers").
- You don't want the work to consume *your* context window.
- Acceptance criteria can be expressed as shell commands (tests, lints, build).
- The work touches a git repo and a branch-per-subtask + automatic merge is acceptable.

## When NOT to use

- A single-shot edit you can finish in one tool call.
- The user is in an exploratory conversation ("what should we do?" — sidekick wants a concrete, executable task).
- The repo isn't a git repo (sidekick needs worktrees).
- The work requires interactive user input mid-flight (sidekick is fully headless, auto-approved).

## Invocation contract

```bash
sidekick --repo <PATH> run "<TASK>" --json [options]
```

- `--repo` defaults to the current directory; pass an absolute path to be unambiguous.
- `<TASK>` is the high-level coding task, in a single quoted string. Be specific: include the acceptance bar (tests pass, lint clean, X works end-to-end).
- `--json` is REQUIRED when calling from a Claude Code session — it implies `--yes` (no interactive confirmation) and emits a single JSON envelope on stdout.
- Options (all optional):
  - `--concurrency N` — max parallel sub-agents (default ~3, tuned by sidekick).
  - `--max-subtasks N` — cap on plan size (default 6).
  - `--approval LEVEL` — `accept_edits_allowlist` (default; safest) | `edits_no_bash` | `bypass` (most permissive).
  - `--model MODEL` — override the model the sub-agents use (default: inherit sidekick's configured model).
  - `--no-vscode` — disable the VSCode auto-open. Pass this when you're orchestrating from a non-interactive context (CI, agentic chain) and don't want windows popping up.
  - **kimi branch only:** `--provider {kimi,claude}` (override default backend), `--kimi-model`, `--kimi-base-url`, `--kimi-key` (override env credentials).

## Response shape (`--json`, `schema_version=1`)

```json
{
  "schema_version": 1,
  "ok": true,
  "exit_code": 0,
  "task": "<the task string>",
  "run_id": "<sidekick run id>",
  "repo_root": "/abs/path/to/repo",
  "wall_ms": 6630,
  "concurrency": 3,
  "approval": "accept_edits_allowlist (no .env/.git/CI; agent may run safe bash)",
  "backend": "kimi:kimi-k2.7-code",
  "n_accepted": 3,
  "n_total": 3,
  "n_merged": 3,
  "progress_path": "/abs/path/to/.sidekick/progress.md",
  "outcomes": [
    {
      "subtask_id":  "s1",
      "title":       "Add --json flag",
      "deps":        [],
      "branch":      "sidekick/s1-add-json-flag",
      "accepted":    true,
      "first_attempt": true,
      "attempts":    1,
      "merged":      true,
      "merge_attempted": true,
      "check_output_tail": "...stdout/stderr tail of acceptance checks..."
    }
  ]
}
```

`backend` is present on the `kimi` branch and omitted on the `claude` branch (which only ever uses native Claude Code sub-sessions). On the kimi branch its value is either `kimi:<model>` or `claude:<model>` depending on the per-run `--provider` setting.

On failure:
- `ok` is `false`, `exit_code` is non-zero.
- `outcomes` still lists every subtask attempted; check `accepted` and `merged` per subtask.
- `check_output_tail` is the last ~2 KB of the acceptance-check transcript — surface it to the user when reporting the failure.

## Hard rules

0. **MINIMAL CHAT.** Output to the user is a short summary of the run + the "what changed" delta. Do NOT echo the entire `outcomes` array verbatim. Do summarize `ok`, `wall_ms`, `n_accepted/n_total`, `n_merged`, and any failing subtask's `title` + `check_output_tail` (last ~20 lines).
1. **ALWAYS pass `--json`** when calling from a Claude Code session. Without it, sidekick spawns a `rich`-styled interactive UI that's unparseable from a sub-session.
2. **ALWAYS pass `--repo` as an absolute path.** Don't rely on cwd — the agent's cwd may not match the user's intent.
3. **Quote the task carefully.** If the task contains double quotes, escape them: `--json` shell-quote rules apply.
4. **One sidekick call per delegation request.** Don't fan out N parallel sidekick calls yourself — sidekick is already parallel internally. If the user wants N truly independent reels/repos, batch via a loop in a single Bash call with `&& wait` or `parallel`, but typically one call is right.
5. **Surface the merged branches.** When `ok=true`, list each subtask's `branch` so the user can `git log --oneline <branch>` if they want to inspect.
6. **On `ok=false`**, the source repo is in whatever state sidekick left it — green subtasks may already be merged. Read `outcomes` carefully: per-subtask `merged: true` means that subtask's branch is in your tree; `merged: false, accepted: true` means the branch exists but didn't merge (conflict?). Either way, don't `reset --hard` without asking the user.

## Workflow

1. Confirm sidekick is on PATH: `command -v sidekick`. If missing, surface the install command: `uv tool install /mnt/backup/projects/sidekick`.
2. Pick a repo. If the user didn't name one, ask. If you're already in one (cwd is a git repo), use it but confirm: "Delegate to sidekick in `<repo>`?".
3. Compose the task string: one concrete sentence + acceptance criteria.
4. Invoke:

   ```bash
   sidekick --repo /abs/path/to/repo run "<task>" --json --no-vscode
   ```

   (Add `--vscode` and drop `--no-vscode` if the user wants the live progress doc to open.)
5. Parse the JSON envelope from stdout. Read `ok`, `n_accepted/n_total`, `outcomes[*].{title,accepted,merged,branch}`, and (kimi branch) `backend`.
6. Report a tight summary:
   - `ok=true`: `wall_ms`, N subtasks merged, list of branches.
   - `ok=false`: which subtasks failed + the last ~20 lines of `check_output_tail`.
7. If the user wants a re-run on the same plan, the previous plan is at `<repo>/.sidekick/state/last_plan.json` — call `sidekick run --plan-file <path> --json`.

## CRITICAL — TOOL CALL DISCIPLINE

- This skill is INVOKED VIA the **Bash** tool — `sidekick run ... --json`. Do NOT improvise an HTTP call, do NOT try to import sidekick as a Python library from this session, and do NOT cd into sidekick's source dir and run `uv run sidekick` (that uses sidekick's own venv, not the installed-on-PATH version).
- `sidekick` IS the right binary; it lives at `~/.local/bin/sidekick` and was installed via `uv tool install`.
- If you need to upgrade sidekick (the user pulled new changes on either branch), the command is `uv tool install --reinstall /mnt/backup/projects/sidekick`.

## See also

- Source: `/mnt/backup/projects/sidekick` — branches `claude` (Claude Code sub-agents) and `kimi` (Kimi sub-agents).
- README has end-to-end design + benchmark numbers (5.4× speedup on the seed benchmark).
- `experiments/bench.py` defines the canonical seed benchmark; `sidekick bench` re-runs it.
