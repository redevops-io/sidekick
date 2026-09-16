---
name: "sidekick-benchmark"
description: "Time-to-Capability benchmark — run the SAME user goal against different products or releases and compare how hard each makes it: elapsed time, interaction count, credential steps, forced onboarding screens and documentation detours. Use when the user wants a release-to-release UX regression check ('did this goal get slower / take more steps?'), a governed-vs-legacy or product-vs-competitor comparison on the same real task, or an actionable UX KPI instead of a subjective score. Every target runs through the identical executor with deterministic success, so the only variable is the product."
license: "Apache-2.0"
version: "0.1.0"
---

# sidekick-benchmark — measure how hard software is to actually operate

`sidekick benchmark` runs one capability against multiple targets through the *same* executor, proving success deterministically on each, and prints a head-to-head. It turns "our onboarding is easier" into a measured, defensible figure.

## When to use
- Compare a user goal across releases to catch UX regressions before they ship.
- Compare your product against a legacy tool or a reference flow on the same task.
- Produce Time-to-Capability as a KPI rather than a contentious 0–100 grade.

## When NOT to use
- A single product / single goal — use `sidekick-test`.
- Targets you aren't authorized to drive. For competitor/legacy runs use a throwaway account, never mutate real data or spend money, and report reference targets honestly as reference models unless you actually drove the vendor.

## Invocation
```bash
sidekick benchmark --capability <key> [--target <name> ...] [--json]
```
- `--capability` — a key from the engine's benchmark recipe (e.g. `cost_audit`).
- `--target` — limit to specific targets; omit to run them all.
- Point a target at a real product by overriding its URL via env (e.g. `BENCH_COST_LEGACY_URL=…`) before the run — no code change.

## Reading the result
A head-to-head table + a one-line takeaway; `--json` gives a per-target Time-to-Capability record (`schema_version: 1`, `mode: "benchmark"`). Example:

| Metric | Product A | Product B |
|---|---|---|
| Successful operation | yes | yes |
| Time-to-Capability | 20.2s | 101.4s |
| User interactions | 3 | 16 |
| Credential steps | 0 | 2 |
| Forced onboarding | 0 | 2 |

Time-to-Capability is measured to the moment the capability is **first reached**, so it reflects the real path length rather than an agent's post-success wandering.

## Requirements
Needs the `ui` extra (browser-use + Playwright + a browser) and an LLM key; self-skips (exit 0) without them. Related: `sidekick-test`, `sidekick-audit`.
