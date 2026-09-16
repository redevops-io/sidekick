---
name: "sidekick-test"
description: "Goal-based end-to-end UI testing that PROVES the outcome. Given a URL and a plain-language goal (e.g. 'sign up, create a project, and invite a teammate'), sidekick drives a real browser to do it and then verifies success with a DETERMINISTIC signal on the real page — the execution agent is never allowed to grade its own work. Use when the user says 'test that a user can …', 'does the signup flow work end to end', 'write an acceptance/smoke test for this journey', or wants to know how many steps a real user journey actually takes. Not for unit tests or static code review."
license: "Apache-2.0"
version: "0.1.0"
---

# sidekick-test — tell it what users should be able to do; it proves whether it worked

`sidekick test` is a mode of the sidekick CLI. It drives a real browser through a natural-language journey, then decides pass/fail with a **deterministic predicate checked on the page** — not the agent's self-report. An agent that clicks around and says "done" without the predicate becoming true is reported as a **failure**.

## When to use
- Verify a real user journey works end to end ("can a user sign up and reach the dashboard?").
- Turn an acceptance criterion or plain-language story into a runnable test.
- Measure journey friction (interactions, credential steps, forced onboarding) as a UX signal.

## When NOT to use
- Unit/integration tests of code (use the normal test runner).
- A goal with no checkable success signal — you must be able to name what "done" looks like on the page.
- Production flows that mutate real data or spend money, unless explicitly authorized (prefer staging).

## Invocation
```bash
sidekick test <url> "<goal>" --expect <predicate> [--step "<sub-step>" ...] [--json]
```
- `--expect` (required) — the deterministic success signal, one of:
  - `usable_state:N` — ≥N focusable/interactive controls exist (the surface is operable)
  - `selector:CSS` — an element matching CSS exists
  - `selector_text:CSS=TEXT` — an element matching CSS contains TEXT (e.g. `selector_text:body=Welcome`)
  - `url_contains:VALUE` — the final URL contains VALUE (e.g. `url_contains:/dashboard`)
- `--step` — ordered sub-steps for a specific path; defaults to the goal itself.
- `--json` — emit a JSON envelope (`schema_version: 1`, `mode: "test"`, `returncode`, `result`) for non-interactive callers.

### Example
```bash
sidekick test https://staging.example.com "Sign up and create your first project" \
  --step "Click Sign up, enter an email and password, submit" \
  --step "On the empty dashboard, click 'New project' and name it Demo" \
  --expect "selector_text:body=Demo" --json
```

## Reading the result
`result` is a Time-to-Capability record: `authenticated_operation_ok` (the real pass/fail), `time_to_capability_s`, `user_interactions`, `credential_steps`, `forced_onboarding_screens`, `documentation_detours`, plus `stuck_points`/`notes`. A non-zero exit means the capability was not deterministically achieved — treat it as a product/journey finding, not a tool error.

## Requirements
Needs the `ui` extra (`uv sync --extra ui && uv run playwright install chromium`) and an LLM key (`KIMI_API_KEY` → `kimi-k2.7-code`, or `OPENAI_API_KEY`). Without them the command self-skips (exit 0). Related: `sidekick-audit`, `sidekick-benchmark`.
