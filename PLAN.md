# loopie — local coding-agent orchestrator

**Goal:** A local orchestrator that decomposes a high-level coding task, fans out to
multiple **auto-approved Claude Code headless sessions** (each on its own git
branch/worktree), shows live progress, and optimizes around measurable speed/accuracy
objectives. Inspired by the **Nous Hermes-Agent** architecture (skills/memory loop, RPC
subagents, pluggable terminal backends) and grounded in **Raschka's six coding-agent
components**.

## Substrate (verified)
- Claude Code native binary at `$CLAUDE_CODE_EXECPATH` → headless via
  `claude -p --output-format stream-json --verbose`, auto-approval via
  `--permission-mode acceptEdits` / `--allow-dangerously-skip-permissions`,
  multi-agent via `--agents`, scoping via `--allowedTools` / `--add-dir`,
  continuity via `--session-id` / `--resume`.
- `git` worktrees give each agent an isolated branch (Hermes "terminal backend" analog).
- Python 3.14 + uv + ruff (matches the sibling `vibexgen` project conventions).

## Architecture (maps Hermes ↔ Raschka)
```
loopie/
  loopie/
    __main__.py / cli.py     CLI: run | plan | status | metrics | bench
    config.py                models, concurrency, paths, env
    repo_context.py          [Raschka #1] git status, tree, docs → workspace summary
    prompts/                 [Raschka #2] stable system-prefix fragments (cache reuse)
    planner.py               task → structured JSON plan (subtask DAG + acceptance checks)
    orchestrator.py          DAG scheduler; concurrency-limited fan-out; merge strategy
    worktree.py              [Hermes backend] git worktree+branch per agent, cleanup
    agent_session.py         [Raschka #3] wraps `claude -p` stream-json subprocess
    approval.py              [Raschka #3] auto-approval policy: mode + tool allowlist + bounds
    events.py                normalize stream-json → progress/metric events
    context_budget.py        [Raschka #4] output clipping + tiered transcript reduction
    memory.py                [Raschka #5] session state as JSON on disk (transcript+working mem)
    skills.py                [Hermes] procedural memory: save/recall reusable skills (FTS)
    metrics.py               objectives: timing, success, tokens → metrics.jsonl
    dashboard.py             live TUI (rich) across all branches + objective readouts
  experiments/bench.py       benchmark harness (baseline vs optimized) on seed tasks
  tests/                     unit tests for planner/events/context_budget/metrics/worktree
  PLAN.md OBJECTIVES.md README.md Justfile pyproject.toml
```

## Execution flow
1. `repo_context.gather()` builds a workspace summary (Raschka #1).
2. `planner.make_plan()` calls Claude headless → JSON plan: subtasks, deps, target
   files, **acceptance checks** (lint/type/test commands). **← plan approval gate.**
3. `orchestrator` builds the DAG, creates a worktree+branch per independent subtask,
   launches `agent_session` per branch with **auto-approval**, capped at N concurrent.
4. Each session streams `stream-json` → `events` → `dashboard` (live) + `metrics`.
5. On finish, run each subtask's acceptance checks; record pass/fail + timings/tokens;
   auto-retry failed subtasks once with the failure context; merge green branches.
6. `memory`/`skills` updated for reuse across runs.

## Measurable objectives (OBJECTIVES.md, enforced by metrics.py + bench.py)
**Speed**
- S1 Orchestration overhead (wall-clock minus agent compute) — target < 8% of run.
- S2 Parallel speedup vs serial baseline on the bench set — target ≥ 2.2× at N=3.
- S3 Time-to-first-edit per agent — target < 20 s.
- S4 Human-wait time — target **0** (full auto-approval).

**Accuracy**
- A1 Acceptance-check pass rate (lint+type+test green) — target ≥ 90% of subtasks.
- A2 First-attempt success (no retry needed) — target ≥ 70%.
- A3 Merge-conflict rate across branches — target < 10%.
- A4 Plan fidelity (subtasks completed as specified) — target ≥ 95%.

**Efficiency**
- E1 Tokens per completed subtask (lower is better; tracked, optimized via cache shape).
- E2 Prompt-cache hit ratio — target ≥ 60% (driven by stable prefix design).

`loopie bench` runs seed tasks serially (baseline) then orchestrated, prints the
objective table, and writes `metrics.jsonl` so every optimization is measured.

## Autonomy contract
Build fully autonomously after this approval. Re-prompt only on **significant
deviations** (e.g., substrate can't support headless auto-approval as designed, or an
objective is structurally unachievable). Default concurrency N=3; auto-approval policy
per the safety level chosen at approval.
```
