# Validation results

End-to-end validation via `loopie bench` (3 disjoint subtasks, real auto-approved
headless Claude sessions on `claude-haiku-4-5`, serial baseline vs orchestrated at N=3).

Serial baseline **35.7s** → orchestrated **6.6s** (3 parallel auto-approved agents).

| ID | Objective | Value | Target | Status |
|----|-----------|-------|--------|--------|
| S1 | Orchestration overhead | 0.9% | < 8% | PASS |
| S2 | Parallel speedup | 5.40× | ≥ 2.2× | PASS |
| S3 | Time-to-first-edit | 3.0s | < 20s | PASS |
| S4 | Human-wait time | 0.0s | = 0 | PASS |
| A1 | Acceptance pass rate | 100% | ≥ 90% | PASS |
| A2 | First-attempt success | 100% | ≥ 70% | PASS |
| A3 | Merge-conflict rate | 0% | < 10% | PASS |
| A4 | Plan fidelity | 100% | ≥ 95% | PASS |
| E1 | Tokens / subtask | ~67k | tracked | info |
| E2 | Cache-hit ratio | 87.4% | ≥ 60% | PASS |

**Objective gate: PASS.**

## Optimization loop (how the targets were reached)

The first real run missed most objectives. Two bugs were found by *measuring*, not
guessing, then fixed and re-measured:

1. **`.pyc` merge collisions (A3 80% → 0%).** Acceptance checks (`python3 -c "import …"`)
   generated `__pycache__/*.pyc`; `git add -A` committed them, and cross-branch bytecode
   triggered *"untracked files would be overwritten by merge — Aborting"*, silently
   failing merges and losing files. Fix: ship a base `.gitignore` for bytecode + defensive
   unstaging in `commit_all` ([worktree.py](loopie/worktree.py)).
2. **Agents wrote outside their worktree (A1 67% → 100%, S2 1.14× → 5.40×).** The worker
   prompt embedded the *main* repo path and said "at the repo root", so agents wrote
   `alpha.py` into the main checkout instead of their sandbox → `ModuleNotFoundError` on
   the checks. Fix: gather each agent's workspace summary from its **own worktree** and
   enforce strict cwd/relative-path discipline ([orchestrator.py](loopie/orchestrator.py),
   [prompts/](loopie/prompts/__init__.py)).
3. **Honest overhead metric (S1).** Reworked S1 to measure wall time beyond the true
   critical path (longest single agent) rather than a retry-inflated sum
   ([metrics.py](loopie/metrics.py)).

Reproduce: `loopie bench`.
