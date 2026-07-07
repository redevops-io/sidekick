# sidekick

A **local coding-agent orchestrator**. Give it a high-level task and it:

1. decomposes the task into a **DAG of subtasks**,
2. fans out one **auto-approved, headless coding agent per subtask** — **any LLM via
   [LiteLLM](https://github.com/BerriAI/litellm)** (OpenAI, Anthropic, Kimi, Gemini, Grok,
   or a **local CPU / Mac-Metal** model) or the **native Claude Code** binary — each
   isolated on its own **git worktree/branch**,
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
| `llm_session.py` + `providers.py` | universal agentic tool loop over **LiteLLM** (any provider/model) + provider presets | Raschka #3 / Hermes |
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

## Provider backends — one codebase, any model (via LiteLLM)

`main` unifies what used to be per-provider branches into a **single, config-driven
backend**. Pick a provider with `--provider` (or `SIDEKICK_PROVIDER`); everything except
`claude` routes through **LiteLLM**, so the same self-contained agentic tool loop
(`read_file`/`write_file`/`edit_file`/`list_dir`/`run_bash`/`finish`) drives *any* model —
only the model string changes. `claude` instead drives the **native Claude Code binary** (a
full agentic harness). Worktrees, auto-approval, dashboard, metrics, and merge are identical
across all of them.

**The default is `local-cpu` — fully offline, no API key.** So out of the box sidekick
targets a local OpenAI-compatible server and nothing leaves the machine.

| `--provider` | Resolves to (LiteLLM) | Key env | Notes |
|---|---|---|---|
| `claude` | native Claude Code CLI | Anthropic auth in the CLI | full harness, not an API call |
| `anthropic` | `anthropic/claude-sonnet-4-5` | `ANTHROPIC_API_KEY` | Claude via API |
| `openai` | `openai/gpt-5-codex` | `OPENAI_API_KEY` | gpt-5/o-series auto-routed by LiteLLM |
| `kimi` | `moonshot/kimi-k2.7-code` | `MOONSHOT_API_KEY` | reasoning model (temp=1) |
| `gemini` | `gemini/gemini-2.5-pro` | `GEMINI_API_KEY` | |
| `grok` | `xai/grok-4` | `XAI_API_KEY` | |
| **`local-cpu`** *(default)* | `openai/<served>` @ `localhost:8080/v1` | — (none) | llama.cpp/vLLM/LM Studio CPU build |
| **`local-metal`** | same, Mac GPU | — (none) | llama.cpp Metal / Ollama / MLX |
| **`cuda`** | `openai/<served>` @ `localhost:8000/v1` | — (none) | NVIDIA GPUs — vLLM NVFP4/llama.cpp CUDA ([`scripts/serve_cuda.justfile`](scripts/serve_cuda.justfile)) |
| `selfhosted` | `openai/<served>` @ `localhost:8000/v1` | — | back-compat alias (evo-x2 vLLM, ROCm) |
| `ollama` | `ollama_chat/qwen2.5-coder:7b` | — | native Ollama |
| *(anything else)* | used as a **raw LiteLLM model** | per provider | e.g. `openrouter/anthropic/claude-3.5-sonnet` |

Overrides (win over any preset): `--model` (LiteLLM string), `--api-base`, `--api-key`,
`--temperature` — or `SIDEKICK_MODEL` / `SIDEKICK_API_BASE` / `SIDEKICK_API_KEY` /
`SIDEKICK_TEMPERATURE`.

```bash
sidekick run "add input validation" --yes                 # local-cpu (default, offline)
sidekick run "..." --provider openai                       # gpt-5-codex (needs OPENAI_API_KEY)
sidekick run "..." --provider claude                       # native Claude Code
sidekick run "..." --provider local-metal --model openai/qwen2.5-coder  # Mac Metal
sidekick run "..." --provider grok --temperature 0.3       # xAI Grok
```

### Running a local model (CPU or Mac Metal)

Any OpenAI-compatible server works — sidekick just needs the `/v1` endpoint:

```bash
# CPU (Linux/mac): llama.cpp server
llama-server -m ./model.gguf --host 0.0.0.0 --port 8080

# Mac Metal (GPU offload): add -ngl 99 to push layers onto the Apple GPU
llama-server -m ./model.gguf --port 8080 -ngl 99
#   …or use Ollama (Metal automatically):  ollama serve  →  --provider ollama
#   …or MLX:  mlx_lm.server --port 8080     →  --provider local-metal
```

On **NVIDIA GPUs**, `--provider cuda` targets a local vLLM/llama.cpp CUDA server on `:8000`.
[`scripts/serve_cuda.justfile`](scripts/serve_cuda.justfile) is a ready recipe — NVFP4
(`nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-NVFP4`) on Blackwell via vLLM with partial CPU
offload, plus a llama.cpp CUDA GGUF fast path — both exposing OpenAI `/v1` with tool-calling:

```bash
just -f scripts/serve_cuda.justfile fetch          # NVFP4 checkpoint  (or: fetch-gguf)
just -f scripts/serve_cuda.justfile serve-vllm-d    # detached vLLM on :8000 (or serve-llamacpp)
sidekick run "..." --provider cuda                  # sidekick points at it
```

`local-cpu`, `local-metal`, and `cuda` are the same wire protocol — the split is
documentation + sensible defaults (endpoint/model); the *build* of your local server is what
actually decides CPU vs Apple-Metal vs NVIDIA-CUDA. llama.cpp ignores the model field (it
serves whatever GGUF is loaded); for vLLM pass the served name with `--model`.

- Same auto-approval policy everywhere: edits auto-approved; `run_bash` gated to the scoped
  allowlist (disabled under `edits_no_bash`, unrestricted under `bypass`).

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

## Communication channels (ported from Hermes 0.17)

sidekick can talk over messaging platforms — **Telegram, Slack, WhatsApp, iMessage** — two
ways. Adapters are dependency-free (stdlib `urllib` / `http.server`), so the whole feature
runs on a $5 VPS.

**Outbound (notify)** — push run start / per-subtask / result to chat:

```bash
export SIDEKICK_TELEGRAM_TOKEN=123:abc SIDEKICK_TELEGRAM_CHAT_ID=42
sidekick run "add input validation" --notify              # all configured channels
sidekick run "…" --notify --channels telegram,slack       # a subset
```

**Inbound (gateway)** — send a coding task *from* chat; sidekick plans, fans out, merges,
and replies with the result:

```bash
sidekick gateway                       # long-running daemon over every configured channel
```

| Channel | Outbound | Inbound | Self-hostable? | Key env |
|---------|----------|---------|----------------|---------|
| **Telegram** | ✅ `sendMessage` | ✅ long-poll `getUpdates` (no public URL) | ✅ fully | `SIDEKICK_TELEGRAM_TOKEN`, `SIDEKICK_TELEGRAM_CHAT_ID` |
| **Slack** | ✅ `chat.postMessage` | ✅ Events API webhook | ✅ (needs reachable webhook) | `SIDEKICK_SLACK_BOT_TOKEN`, `SIDEKICK_SLACK_CHANNEL` |
| **WhatsApp** | ✅ Cloud API | ✅ Meta webhook (`hub.challenge`) | ⚠️ needs Meta Business acct + public URL | `SIDEKICK_WHATSAPP_TOKEN`, `SIDEKICK_WHATSAPP_PHONE_ID`, `SIDEKICK_WHATSAPP_VERIFY_TOKEN` |
| **iMessage** | ✅ relay command | ✅ relay inbox file | ⚠️ needs a Mac relay (BlueBubbles/AppleScript) | `SIDEKICK_IMESSAGE_SEND_CMD`, `SIDEKICK_IMESSAGE_INBOX` |

Slack/WhatsApp use webhooks, so the gateway also starts a small HTTP server
(`--http-host`/`--http-port`, default `0.0.0.0:8787`) to receive callbacks; Telegram and
iMessage are polled and need no inbound port.

**Safety.** Inbound messages trigger *auto-approved* coding runs, so the gateway is **closed
by default**: it acts only on senders listed in `SIDEKICK_GATEWAY_ALLOW` (comma-separated
handles). Set `SIDEKICK_GATEWAY_OPEN=1` to accept anyone — only on a trusted, private box.

`SIDEKICK_CHANNELS` (comma-separated) selects which adapters to load; unset = all that have
credentials present. A channel missing its tokens is silently skipped.

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
parent session never spends its own context on the work. On this `selfhosted`
branch the spawned sub-agents run on the local model (vLLM/llama.cpp `/v1`) by
default; the parent Claude Code session shells out via a single CLI call and
parses a JSON envelope back.

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
(`selfhosted:<model>` or `claude:<model>` per the per-run `--provider` choice),
per-subtask `branch`, and the last 2 KB of each acceptance check's transcript.
Enough for the parent to summarize and decide whether to follow up.

On this branch the default backend is the local self-hosted model (vLLM/llama.cpp);
pass `--provider claude` to delegate to native Claude Code sub-agents instead. Either way
the envelope shape is identical — only the `backend` field changes.
