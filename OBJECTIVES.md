# loopie — measurable objectives

Every objective is computed by `loopie/metrics.py` from `metrics.jsonl` and printed by
`loopie metrics` / `loopie bench`. Targets are enforcement gates: `bench` exits non-zero
if a hard target regresses.

## Speed
| ID | Metric | Definition | Target |
|----|--------|------------|--------|
| S1 | Orchestration overhead | `wall_clock - sum(agent_compute_overlap_adjusted)` as % of wall_clock | < 8% |
| S2 | Parallel speedup | serial-baseline wall_clock / orchestrated wall_clock at N=3 | ≥ 2.2× |
| S3 | Time-to-first-edit | median `ttft`/first tool_use per agent | < 20 s |
| S4 | Human-wait time | seconds spent waiting on manual approval | 0 |

## Accuracy
| ID | Metric | Definition | Target |
|----|--------|------------|--------|
| A1 | Acceptance pass rate | subtasks whose acceptance checks (lint/type/test) pass | ≥ 90% |
| A2 | First-attempt success | subtasks passing without a retry | ≥ 70% |
| A3 | Merge-conflict rate | branches that fail to merge cleanly | < 10% |
| A4 | Plan fidelity | subtasks completed as specified vs planned | ≥ 95% |

## Efficiency
| ID | Metric | Definition | Target |
|----|--------|------------|--------|
| E1 | Tokens / subtask | total tokens / completed subtasks (tracked, minimized) | tracked |
| E2 | Cache-hit ratio | cache_read / (input + cache_read + cache_creation) | ≥ 60% |

## How optimization is driven
`loopie bench` runs the seed task set twice — serial baseline, then orchestrated — and
emits the objective table plus a per-run `metrics.jsonl`. Each change to prompt shape,
context budget, concurrency, or approval policy is judged by its effect on this table,
not by intuition. The cache-shaped prompt prefix (Raschka #2) is the primary lever for
E2; worktree isolation is the primary lever for S2/A3; auto-approval is the lever for S4.
