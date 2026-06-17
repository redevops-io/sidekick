# loopie task runner

# Install dev deps into a uv venv
setup:
    uv venv
    uv pip install -e ".[dev]"

# Lint
lint:
    uv run ruff check loopie experiments tests

# Format
fmt:
    uv run ruff format loopie experiments tests

# Run unit tests (no network/agent calls)
test:
    uv run pytest

# Decompose and run a task in the current repo (auto-approved fan-out)
run task:
    uv run loopie run "{{task}}" --yes

# Print the plan only
plan task:
    uv run loopie plan "{{task}}"

# Objective table
metrics:
    uv run loopie metrics

# Seed benchmark (serial baseline vs orchestrated)
bench:
    uv run loopie bench
