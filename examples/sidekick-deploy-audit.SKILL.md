---
name: "sidekick-deploy-audit"
description: "End-to-end proof that a user can follow the redevops.io/projects instructions, stand up the whole agentic apps stack, and actually use it. Extracts the documented deploy commands from the LIVE /projects/apps-install page (so it also catches when the published instructions drift), deploys an isolated ephemeral copy (unique compose project + remapped ports + your model endpoint — never touching the live demos), waits until it's healthy, then navigates every surface: a deterministic UI-quality audit (WCAG 2.2 / Core Web Vitals / browser health) plus browser-use usability journeys with deterministic success predicates, and tears the stack down. Use when you want to verify the whole install-and-use experience, not just one page. Deterministic deploy-health + audit verdicts outrank any agent's self-report."
license: "Apache-2.0"
version: "0.1.0"
---

# sidekick-deploy-audit — deploy the stack per the docs, then audit + use every function

`sidekick deploy-audit` is the full-journey check: it does what a new user is told to do on
**redevops.io/projects**, then proves the result is usable. It combines a **docs-accuracy gate**, a **real
(isolated) deploy**, the deterministic **audit** (`sidekick-audit`), and **usability journeys**
(`sidekick-test`) into one run.

## When to use
- Verify that the *published* install instructions actually work end-to-end (a fresh deploy, not a page that
  is already running) — and catch it when the docs drift from reality.
- Audit + exercise **every surface** of a freshly deployed stack (console, domain-agent dashboards, Twenty,
  Metabase, Postiz) in one pass.
- Regression-guard the whole install-and-use experience before a release.

## Invocation
```bash
sidekick deploy-audit --host proxmox --llm-base-url http://<evo-x2>:<port>/v1   # or $STACK_LLM_BASE_URL
sidekick deploy-audit --host local --strict --json      # fail on any confirmed blocker; JSON envelope
sidekick deploy-audit --keep                            # leave the ephemeral stack up to inspect (debug)
```
- `--host` — ssh alias (default `proxmox`) or `local` — where `docker compose` runs.
- `--public-host` — host/ip a browser uses to reach the stack (default `192.168.40.105`).
- `--llm-base-url` / `$STACK_LLM_BASE_URL` — the agents' OpenAI-compatible model endpoint (e.g. evo-x2's Qwen).

## What it does (and why it's safe)
1. **Docs gate** — reads the live `/projects/apps-install` page, extracts the documented command sequence via
   the page's own RUN/SKIP rules, and fails if the canonical `git clone … agentic-os-enterprise` + `docker
   compose up` sequence is gone.
2. **Isolated deploy** — clones the documented repo and `docker compose up`s `deploy/apps` under a **unique
   `agentic-eph-*` project**, **remapped host ports**, an ephemeral clone dir, and your model endpoint. The
   live redevops.io demos are never touched; teardown refuses any non-ephemeral project.
3. **Health** — waits for the console front to answer (the deterministic go/no-go).
4. **Audit + journeys** — every surface gets the deterministic audit **and** a usability journey with a
   deterministic success predicate; the agent never grades its own success.
5. **Teardown** — brings the ephemeral stack down and removes the clone, then confirms a live demo is still up.

## Reading the result
The JSON envelope (`--json`, `schema_version:1`) carries `healthy`, per-surface audit verdicts (dimensions +
confirmed-vs-uncertain findings clustered into root-cause families), the usability `journeys` (Time-to-
Capability with a deterministic `authenticated_operation_ok`), and `blockers`. Exit is non-zero if the stack
never became healthy, or (with `--strict`) if any **confirmed** blocker is present.

## Requirements
The `ui` extra (Playwright + a browser) and a Kimi/OpenAI key for the journeys; docker on the target host and
a reachable model endpoint for the deploy. Missing any of these → the engine self-skips (exit 0) rather than
failing. Heavy (a full stack comes up) — run it on demand, not in a fast CI lane. Related: `sidekick-audit`,
`sidekick-test`, `sidekick-benchmark`.
