# Contributing

Issues and pull requests are welcome: <https://github.com/redevops-io/sidekick/issues>.

## Setup and tests
```bash
git clone https://github.com/redevops-io/sidekick.git && cd sidekick
just setup            # uv venv + editable install with dev extra
just test         # uv run pytest
```
Do not install the `ui` extra: it needs a private repository.

## Guidelines
- Keep changes small and focused; add a test in `tests/` for behaviour changes.
- Do not commit secrets (a secret-scan workflow runs).
- Any performance number in docs must link to raw data in this repo or carry an evidence label (Illustrative/synthetic, Internally reproduced, ...).
- Contributions are under the repository licence ([LICENSE.md](LICENSE.md): AGPL-3.0-or-later with the Commons Clause).
