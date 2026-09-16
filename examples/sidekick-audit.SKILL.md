---
name: "sidekick-audit"
description: "Deterministic UI quality audit of a web page or a whole product — WCAG 2.2 A/AA accessibility (axe-core), Core Web Vitals (LCP/CLS), browser health (console errors, failed requests, mixed content) and tap-target size — reported dimensions-first, with confirmed-vs-uncertain findings clustered into root-cause families ('N findings across S surfaces -> M implementation defects'). Use when the user asks to check a URL or product for accessibility / performance / robustness problems, wants an engineering-actionable defect list rather than a raw scanner dump, or wants CI to fail on confirmed blockers. Deterministic evidence outranks model opinion."
license: "Apache-2.0"
version: "0.1.0"
---

# sidekick-audit — deterministic UI quality, findings that map to defects

`sidekick audit` runs a real-browser deterministic pass. It separates what it **confirmed** from what a human still must **review**, and states that automated tools catch only ~30–40% of accessibility issues.

## When to use
- Check a page or product for accessibility (WCAG 2.2 A/AA), Core Web Vitals, and browser-health problems.
- Get a *defect* list (root-cause families) instead of a raw finding dump.
- Gate CI on confirmed blockers; keep noisy/uncertain findings as review items.
- Re-audit after a fix and assert a specific defect never returns.

## Invocation
```bash
sidekick audit <url> [--type <service_type>]        # one page
sidekick audit --product <key>                      # every target of a product
sidekick audit --all [--json]                       # the whole registered portfolio
sidekick audit --all --assert-regressions store.json   # CI: fail if a fixed defect regresses
```
`--type` selects the rubric (`Landing/Marketing`, `Dashboard/Console`, `Conversational-AI`, `Auth-flow`, `Data/Analytics`, `Docs/Onboarding`) and belongs to the target, not the app.

## Reading the result (dimensions first, index second)
Each target yields a verdict with `deterministic` dimensions (`accessibility`, `functionality`, `performance`, `browser_health`, and `task_completion` when a journey ran), a **secondary** `overall_index`, and a `status`:
- **NEEDS_WORK** — a **confirmed** blocker (a certain WCAG-A violation, a broken primary control, a `poor` Core Web Vital, a failed task).
- **REVIEW_REQUIRED** — an **uncertain** finding (axe "needs-review", heuristic concern) → routed to a human, never auto-failed.
- **PASS** — no confirmed blocker, no material open concern.

The report leads with **"N findings across S surfaces → M implementation defects"**, clustering confirmed findings into named families (e.g. `ARIA_TAB_PATTERN`, `INTERACTIVE_COLOR_INHERITANCE`, `DOCUMENT_LANGUAGE`) so a team sees how many things they actually have to fix. Every finding carries `certainty` and a stable `signature = criterion@target@surface`.

## CI
Non-zero exit on a confirmed NEEDS_WORK. With `--assert-regressions`, a previously-fixed defect that reappears also fails the build — independent of the aggregate score.

## Requirements
Needs the `ui` extra (Playwright + a browser); axe-core is injected from a pinned CDN. **No LLM key required** for the deterministic pass. Read-only — it never submits forms or mutates state. Related: `sidekick-test`, `sidekick-benchmark`.
