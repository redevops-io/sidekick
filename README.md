# sidekick

A **local coding-agent orchestrator**. Give it a high-level task and it:

1. decomposes the task into a **DAG of subtasks**,
2. fans out one **auto-approved, headless coding agent per subtask** (Claude Code or — on
   this `kimi` branch by default — Kimi via Moonshot `/v1`), each isolated on its own
   **git worktree/branch**,
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

| Source | What sidekick takes from it |
|--------|---------------------------|
| **Nous Hermes-Agent** | skills/memory learning loop, pluggable execution backend (git worktrees here), isolated subagent delegation for parallel workstreams |
| **Raschka, "The Six Components of a Coding Agent"** | (1) live repo context, (2) cache-shaped prompts, (3) bounded/structured tool use, (4) context-bloat control, (5) structured session memory, (6) bounded subagent delegation |

### Component map
| Module | Role | Lineage |
|--------|------|---------|
| `repo_context.py` | workspace summary (branch, tree, docs) | Raschka #1 |
| `prompts/` | stable system prefix + dynamic suffix for cache reuse | Raschka #2 |
| `agent_session.py` + `approval.py` | headless `claude -p` wrapper, auto-approval policy | Raschka #3 |
| `kimi_session.py` | native Kimi (Moonshot) `/v1` agentic tool loop | Raschka #3 / Hermes |
| `context_budget.py` | output clipping + tiered transcript reduction | Raschka #4 |
| `memory.py` | transcript + working memory as JSON on disk | Raschka #5 |
| `orchestrator.py` | DAG waves, bounded parallel agents, merge | Raschka #6 / Hermes |
| `worktree.py` | git worktree+branch per agent | Hermes backend |
| `skills.py` | distill + recall reusable skills | Hermes learning loop |
| `dashboard.py` | live `rich` table + live `progress.md` | — |
| `vscode.py` | open progress doc + changed files in VSCode | — |
| `metrics.py` | objective computation + gate | OBJECTIVES.md |

## How auto-approval works

sidekick drives the Claude Code **native binary** (`$CLAUDE_CODE_EXECPATH`) in headless
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

## Provider backends (this is the `kimi` branch)

sidekick supports pluggable agent execution backends, selected by `--provider` (or
`SIDEKICK_PROVIDER`). **On the `kimi` branch the default is `kimi`** — both task *planning*
and agent *execution* run on Moonshot's Kimi via the OpenAI-compatible `/v1` API, through a
self-contained agentic tool loop (`read_file`/`write_file`/`edit_file`/`list_dir`/`run_bash`/
`finish`), reusing sidekick's worktrees, auto-approval, dashboard, metrics, and merge.

Credentials are read from the **host environment by default**, or provided **manually** per
run:

| Var (host default) | Manual override | Default |
|--------------------|-----------------|---------|
| `KIMI_AGENT_BASE_URL` | `--kimi-base-url` | `https://api.moonshot.ai/v1` |
| `KIMI_AGENT_MODEL` | `--kimi-model` | `kimi-k2.7-code` |
| `KIMI_AGENT_API_KEY` | `--kimi-key` | — (required) |
| `SIDEKICK_PROVIDER` | `--provider` | `kimi` |

```bash
sidekick run "add input validation" --yes                 # uses Kimi (host env)
sidekick run "..." --provider claude                       # fall back to Claude Code
sidekick run "..." --kimi-model kimi-k2.7-code --kimi-key sk-…  # manual creds
```

Notes:
- `kimi-k2.x` are **reasoning models**: sidekick sends `temperature=1` (the only value they
  accept) and they think before acting, so planning/first-token latency is higher than
  Claude's but token cost per subtask is markedly lower.
- The same auto-approval policy applies: edits are auto-approved; `run_bash` is gated to
  the scoped allowlist (or disabled under `edits_no_bash`, unrestricted under `bypass`).
- Branch model: shared features (voice, orchestration, metrics) live on `claude` and merge
  into provider branches; `openai`/`grok` branches follow the same pattern via their keys.

## Showing progress in VSCode

The spawned agents are **headless** Claude Code subprocesses — that is what makes
auto-approval and parallel fan-out possible, and it means they do **not** appear as
interactive sessions in the VSCode sidebar (that sidebar session is the one you use to
*drive* sidekick). Progress is surfaced in the editor instead:

- The dashboard writes a live **`progress.md`** (`.sidekick/runs/<id>/progress.md`) — a
  per-agent table of status / current action / edits / turns / tokens / elapsed, plus a
  result footer. sidekick opens it with `code -r`; VSCode auto-reloads the tab on every
  update, so you watch the whole fan-out from one editor pane.
- On completion, the **changed files** of each accepted subtask are opened for review.
- Detection is automatic (the `code` CLI on PATH); force with `--vscode` / `--no-vscode`
  or `SIDEKICK_VSCODE=1|0`. The same `progress.md` works in any editor or `tail`.

You can run sidekick from VSCode's integrated terminal (or its Claude Code extension
terminal) and keep `progress.md` open beside it.

## Make sidekick your default coding workflow in VSCode

**First, put sidekick on PATH** so VSCode tasks and terminals can call it:
```bash
cd /path/to/sidekick && uv tool install --editable .   # or: pipx install -e .
```

**The one limitation:** the Claude Code **sidebar session cannot be transparently
rerouted** through sidekick — the extension runs the agent in-process and exposes no reroute
hook, so nothing can sit invisibly underneath it. sidekick *drives* headless agents; you
keep using the sidebar to drive sidekick. With that understood, there are three usage
patterns:

### Pattern A — auto-launch `sidekick repl` on folder open (the "default workflow")
Copy [`examples/vscode/tasks.json`](examples/vscode/tasks.json) → `.vscode/tasks.json` in
any repo. Opening the folder auto-starts **`sidekick repl`** in a dedicated terminal (VSCode
asks once to "Allow Automatic Tasks"). Then, every time:
```
sidekick> add input validation to the upload handler
```
→ sidekick plans → fans out auto-approved agents on worktrees → merges green branches, with
`progress.md` live in an editor tab beside you. This is the closest thing to "VSCode always
runs through sidekick."

### Pattern B — drive sidekick from the sidebar session
Use the interactive Claude Code sidebar normally; when a task wants parallel fan-out, tell
it to run sidekick, e.g. *"run `sidekick run "refactor X across these 4 modules" --yes`"*. The
sidebar stays your control surface; sidekick owns the parallel execution and reports back via
`progress.md` + opened diffs.

### Pattern C — terminal one-liners / hotkey
- Integrated-terminal alias (add to `~/.bashrc`):
  ```bash
  cc() { sidekick run "$@" --yes; }   # then:  cc "add unit tests for parser.py"
  ```
  Recursion-safe — sidekick invokes the Claude binary by its absolute `$CLAUDE_CODE_EXECPATH`,
  never the shell `claude`.
- Hotkey: [`examples/vscode/keybindings.json`](examples/vscode/keybindings.json) binds
  `Ctrl+Alt+L` to a "run task" prompt (uses the second task in `tasks.json`).

### What you see in the editor
| Surface | When | Where |
|---------|------|-------|
| Live `progress.md` (per-agent table) | during the run | editor tab, auto-reloads |
| `rich` dashboard | during the run | the `sidekick` terminal |
| Changed files of each accepted subtask | on completion | opened for review |
| Objective table (S1–S4 / A1–A4 / E1–E2) | on completion | the `sidekick` terminal |

Toggle the editor integration with `--vscode` / `--no-vscode` or `SIDEKICK_VSCODE=1|0`
(auto-detected from the `code` CLI). A per-agent VSCode **window** on each worktree is not
opened by default — set it up with a `code <worktree>` step if you want one window per agent.

## Voice input

Speak a task instead of typing it — works in the VSCode integrated terminal (it uses the
OS mic via `ffmpeg`/`arecord`, which the terminal process can access).

```bash
sidekick voice                 # press Enter, speak, sidekick plans → fans out → merges
sidekick voice --transcribe-only   # just print what it heard
sidekick repl --voice          # voice-driven interactive loop (great for the VSCode task)
```

Speech-to-text goes through an **OpenAI-compatible `/audio/transcriptions`** endpoint, so
it is provider-independent from the coding model (shared by the `claude`, `kimi`, … branches):

| Var | Default |
|-----|---------|
| `SIDEKICK_STT_BASE_URL` | `$OPENAI_BASE_URL` or `https://api.openai.com/v1` |
| `SIDEKICK_STT_API_KEY` | `$OPENAI_API_KEY` |
| `SIDEKICK_STT_MODEL` | `whisper-1` |
| `SIDEKICK_AUDIO_INPUT` | auto (`pulse:default` / `alsa:default`) |
| `SIDEKICK_AUDIO_SECONDS` | `8` |

Requires `ffmpeg` or `arecord` plus an STT key; degrades gracefully with a clear message
if either is missing.

## Usage

```bash
just setup                              # uv venv + editable install
sidekick plan "add input validation"      # see the subtask DAG
sidekick run "add input validation" --yes # fan out, auto-approve, merge, report
sidekick repl                             # interactive task loop (VSCode auto-launch)
sidekick voice                            # speak a task; sidekick runs it
sidekick repl --voice                     # voice-driven loop
sidekick metrics                          # objective table from .sidekick/metrics.jsonl
sidekick status                           # last run's working memory
sidekick bench                            # serial baseline vs orchestrated (proves S2)

sidekick run "..." --vscode               # force-open progress + diffs in VSCode
sidekick run "..." --no-vscode            # terminal dashboard only
sidekick run "..." --concurrency 5        # wider fan-out
```

Run `sidekick` from inside the target git repository (changes are made to that repo's
branches and merged into its current branch).

## Measurable objectives

See [OBJECTIVES.md](OBJECTIVES.md). `sidekick bench` and `sidekick metrics` compute S1–S4
(speed), A1–A4 (accuracy), E1–E2 (efficiency) from `metrics.jsonl`. Every optimization
(prompt shape, context budget, concurrency, approval policy) is judged by its effect on
this table.

## Requirements

- Claude Code native binary on PATH or `$CLAUDE_CODE_EXECPATH` (set inside Claude Code).
- `git`, Python ≥ 3.12. `rich` (declared dep) for the live dashboard.
- Auth via the running Claude Code session's credentials (or `ANTHROPIC_API_KEY`).
- *Optional:* the VSCode `code` CLI for the in-editor progress doc + diffs (auto-detected;
  sidekick runs fine without it).

## Delegation from another Claude Code session

`sidekick` can be invoked **from inside another Claude Code session** so the
parent session never spends its own context on the work. On this `kimi` branch
the spawned sub-agents are Kimi (Moonshot `/v1`) sessions by default; the
parent Claude Code session shells out via a single CLI call and parses a JSON
envelope back.

Two pieces:

1. **Install once, globally**:

   ```bash
   uv tool install /mnt/backup/projects/sidekick
   ```

   Puts `sidekick` on `PATH` for every shell — every Claude Code session
   (current or future) can shell out to it. Rerun with `--reinstall` after
   pulling new changes:

   ```bash
   uv tool install --reinstall /mnt/backup/projects/sidekick
   ```

2. **The user-level skill** at `~/.claude/skills/delegate-to-sidekick/SKILL.md`
   (canonical copy in this branch at `examples/delegate-to-sidekick.SKILL.md`)
   teaches any Claude Code session **when** to delegate, **how** to invoke,
   and **what envelope** to expect. Auto-discovered by Claude Code via the
   `~/.claude/skills/` user-skills dir — the parent session will see
   `delegate-to-sidekick` in its available-skills list and call it via the
   `Skill` tool.

The contract is one command (the parent session runs it via Bash):

```bash
sidekick --repo /abs/path/to/repo run "<task>" --json --no-vscode
```

- `--json` emits a single envelope on stdout, schema_version=1 (docs in the
  SKILL.md). Implies `--yes`. Without this, sidekick spawns a `rich` UI that's
  unparseable from a sub-session.
- `--no-vscode` keeps the live progress doc from popping a window into the
  parent's IDE.

The envelope carries `ok`, `n_accepted/n_total`, `n_merged`, the `backend` used
(`kimi:<model>` or `claude:<model>` per the per-run `--provider` choice),
per-subtask `branch`, and the last 2 KB of each acceptance check's transcript.
Enough for the parent to summarize and decide whether to follow up.

On this branch the default backend is Kimi (Moonshot); pass `--provider claude`
to delegate to native Claude Code sub-agents instead. Either way the envelope
shape is identical — only the `backend` field changes.
