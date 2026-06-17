# loopie

A **local coding-agent orchestrator**. Give it a high-level task and it:

1. decomposes the task into a **DAG of subtasks**,
2. fans out one **auto-approved, headless Claude Code session per subtask**, each isolated
   on its own **git worktree/branch**,
3. shows **live progress** — both a terminal `rich` table and a live `progress.md`
   document that opens in **VSCode** and auto-reloads as agents work,
4. runs each subtask's **acceptance checks** (retrying once on failure),
5. **merges** the green branches and opens the changed files in VSCode,
6. reports a table of **measurable speed/accuracy objectives**.

Validated end to end: serial baseline **35.7s → orchestrated 6.6s** (5.40× speedup), 100%
acceptance, 0 merge conflicts, 0 human-wait — see [RESULTS.md](RESULTS.md).

It is built fully autonomously after a single plan approval, and re-prompts only on
significant deviations.

## Design lineage

| Source | What loopie takes from it |
|--------|---------------------------|
| **Nous Hermes-Agent** | skills/memory learning loop, pluggable execution backend (git worktrees here), isolated subagent delegation for parallel workstreams |
| **Raschka, "The Six Components of a Coding Agent"** | (1) live repo context, (2) cache-shaped prompts, (3) bounded/structured tool use, (4) context-bloat control, (5) structured session memory, (6) bounded subagent delegation |

### Component map
| Module | Role | Lineage |
|--------|------|---------|
| `repo_context.py` | workspace summary (branch, tree, docs) | Raschka #1 |
| `prompts/` | stable system prefix + dynamic suffix for cache reuse | Raschka #2 |
| `agent_session.py` + `approval.py` | headless `claude -p` wrapper, auto-approval policy | Raschka #3 |
| `context_budget.py` | output clipping + tiered transcript reduction | Raschka #4 |
| `memory.py` | transcript + working memory as JSON on disk | Raschka #5 |
| `orchestrator.py` | DAG waves, bounded parallel agents, merge | Raschka #6 / Hermes |
| `worktree.py` | git worktree+branch per agent | Hermes backend |
| `skills.py` | distill + recall reusable skills | Hermes learning loop |
| `dashboard.py` | live `rich` table + live `progress.md` | — |
| `vscode.py` | open progress doc + changed files in VSCode | — |
| `metrics.py` | objective computation + gate | OBJECTIVES.md |

## How auto-approval works

loopie drives the Claude Code **native binary** (`$CLAUDE_CODE_EXECPATH`) in headless
mode:

```
claude -p "<prompt>" --output-format stream-json --verbose \
       --permission-mode acceptEdits \
       --allowedTools "Edit Write Read Grep Glob Bash(uv *) Bash(pytest *) …" \
       --session-id <uuid> --max-turns N
```

`acceptEdits` + an explicit `--allowedTools` allowlist auto-approves edits and a scoped
set of build/test/lint/vcs commands while still refusing unlisted or dangerous
operations — zero human prompts (objective **S4 = 0**), without an open shell grant.
Each session runs inside its own worktree, so parallel agents never collide.

Three approval levels (`--approval`): `accept_edits_allowlist` (default), `bypass`
(`--allow-dangerously-skip-permissions`), `edits_no_bash`.

## Showing progress in VSCode

The spawned agents are **headless** Claude Code subprocesses — that is what makes
auto-approval and parallel fan-out possible, and it means they do **not** appear as
interactive sessions in the VSCode sidebar (that sidebar session is the one you use to
*drive* loopie). Progress is surfaced in the editor instead:

- The dashboard writes a live **`progress.md`** (`.loopie/runs/<id>/progress.md`) — a
  per-agent table of status / current action / edits / turns / tokens / elapsed, plus a
  result footer. loopie opens it with `code -r`; VSCode auto-reloads the tab on every
  update, so you watch the whole fan-out from one editor pane.
- On completion, the **changed files** of each accepted subtask are opened for review.
- Detection is automatic (the `code` CLI on PATH); force with `--vscode` / `--no-vscode`
  or `LOOPIE_VSCODE=1|0`. The same `progress.md` works in any editor or `tail`.

You can run loopie from VSCode's integrated terminal (or its Claude Code extension
terminal) and keep `progress.md` open beside it.

## Usage

```bash
just setup                              # uv venv + editable install
loopie plan "add input validation"      # see the subtask DAG
loopie run "add input validation" --yes # fan out, auto-approve, merge, report
loopie metrics                          # objective table from .loopie/metrics.jsonl
loopie status                           # last run's working memory
loopie bench                            # serial baseline vs orchestrated (proves S2)

loopie run "..." --vscode               # force-open progress + diffs in VSCode
loopie run "..." --no-vscode            # terminal dashboard only
loopie run "..." --concurrency 5        # wider fan-out
```

Run `loopie` from inside the target git repository (changes are made to that repo's
branches and merged into its current branch).

## Measurable objectives

See [OBJECTIVES.md](OBJECTIVES.md). `loopie bench` and `loopie metrics` compute S1–S4
(speed), A1–A4 (accuracy), E1–E2 (efficiency) from `metrics.jsonl`. Every optimization
(prompt shape, context budget, concurrency, approval policy) is judged by its effect on
this table.

## Requirements

- Claude Code native binary on PATH or `$CLAUDE_CODE_EXECPATH` (set inside Claude Code).
- `git`, Python ≥ 3.12. `rich` (declared dep) for the live dashboard.
- Auth via the running Claude Code session's credentials (or `ANTHROPIC_API_KEY`).
- *Optional:* the VSCode `code` CLI for the in-editor progress doc + diffs (auto-detected;
  loopie runs fine without it).
