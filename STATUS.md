# Status

| | |
|---|---|
| Maturity | alpha |
| Version | 0.1.0 (single tag `v0.1.0`) |
| Last git activity | 2026-09-27 (`git log -1 --format=%cs`; clone is shallow) |
| Distribution | Clone and `uv tool install --editable .`. Not published for install via `pip install sidekick` (that PyPI name is an unrelated project). |

## Supported
- DAG planning, parallel auto-approved sub-sessions on git worktrees, acceptance checks, merge, progress dashboard (`sidekick plan` / `run` / `repl` / `status` / `metrics`).
- Providers: native Claude Code (`--provider claude`) and any LiteLLM model; default is `local-cpu` (a local OpenAI-compatible server on `:8080`). See README "Requirements".
- Tests in `tests/` (no CI job runs them; the only workflow is a secret scan plus a docs link check).

## Not supported / not claimed
- The benchmark figure (serial 35.7s vs orchestrated 6.6s, 5.40x) is **Illustrative/synthetic (single run)**: toy task, 3 agents (linear ceiling 3x), raw metrics not in repo. No third-party reproduction.
- The `ui` extra (`sidekick test` / `audit` / `benchmark`) depends on the private `agentic-tests` repository and cannot be installed by the public.

## Known issues
- `RESULTS.md` says to run `sidekick bench --provider claude`, but the `bench` subcommand defines no `--provider` option (per code reading); use `SIDEKICK_PROVIDER=claude`.
- `sidekick deploy-audit` takes a `--public-host` default that is an internal IP in the CLI source (`sidekick/cli.py`, `sidekick/ui.py`); the example skill doc now shows a placeholder. Not changed in code by the docs PR.
- The name "Sidekick" is used for several different things across ReDevOps repos; this repo is the coding-agent orchestrator only.
