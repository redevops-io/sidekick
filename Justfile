# sidekick task runner

# Install dev deps into a uv venv
setup:
    uv venv
    uv pip install -e ".[dev]"

# Lint
lint:
    uv run ruff check sidekick experiments tests

# Format
fmt:
    uv run ruff format sidekick experiments tests

# Run unit tests (no network/agent calls)
test:
    uv run pytest

# Decompose and run a task in the current repo (auto-approved fan-out)
run task:
    uv run sidekick run "{{task}}" --yes

# Print the plan only
plan task:
    uv run sidekick plan "{{task}}"

# Objective table
metrics:
    uv run sidekick metrics

# Seed benchmark (serial baseline vs orchestrated)
bench:
    uv run sidekick bench
