"""Configuration and path resolution for sidekick."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path


def _resolve_claude_bin() -> str:
    """Locate the Claude Code executable.

    Prefers CLAUDE_CODE_EXECPATH (set when running inside Claude Code), falls back to a
    `claude` on PATH. Returns the raw string even if unresolved so callers can surface a
    clear error at spawn time.
    """
    env = os.environ.get("CLAUDE_CODE_EXECPATH")
    if env and Path(env).exists():
        return env
    found = shutil.which("claude")
    return found or env or "claude"


# Auto-approval levels (Raschka #3: bounded, structured tool use).
APPROVAL_ACCEPT_EDITS_ALLOWLIST = "accept_edits_allowlist"
APPROVAL_BYPASS = "bypass"
APPROVAL_EDITS_NO_BASH = "edits_no_bash"

# Default Bash command prefixes auto-approved under the allowlist policy. Scoped to
# read/build/test/lint/vcs operations — never an open `Bash` grant.
DEFAULT_BASH_ALLOWLIST = (
    "Bash(uv *)",
    "Bash(python *)",
    "Bash(python3 *)",
    "Bash(pytest *)",
    "Bash(ruff *)",
    "Bash(git status*)",
    "Bash(git diff*)",
    "Bash(git add*)",
    "Bash(git log*)",
    "Bash(ls *)",
    "Bash(cat *)",
    "Bash(rg *)",
    "Bash(grep *)",
    "Bash(just *)",
    "Bash(make *)",
    "Bash(node *)",
    "Bash(npm *)",
)

DEFAULT_EDIT_TOOLS = ("Edit", "Write", "Read", "Grep", "Glob", "TodoWrite")


@dataclass
class Config:
    """Runtime configuration for an orchestration run."""

    repo_root: Path
    claude_bin: str = field(default_factory=_resolve_claude_bin)
    # Agent execution backend. "claude" = Claude Code headless; "selfhosted" = a local
    # OpenAI-compatible server (vLLM / llama.cpp) over /v1. On this `selfhosted` branch the
    # default is "selfhosted"; override with SIDEKICK_PROVIDER or --provider.
    provider: str = field(default_factory=lambda: os.environ.get("SIDEKICK_PROVIDER", "selfhosted"))
    # Self-hosted backend — a local vLLM (or llama.cpp) server exposing the OpenAI /v1 API.
    # Defaults target a vLLM instance on the local evo-x2 (Strix Halo) box; no cloud key.
    # NB: deliberately does NOT fall back to OPENAI_BASE_URL / OPENAI_API_KEY. This branch
    # is local-by-default — a stray cloud key in the host env must never silently reroute
    # inference (and cost) off the evo-x2 box. Override only via the explicit vars below.
    vllm_base_url: str = field(
        default_factory=lambda: os.environ.get("SIDEKICK_AGENT_BASE_URL")
        or os.environ.get("VLLM_BASE_URL")
        or "http://localhost:8000/v1"
    )
    vllm_model: str = field(
        default_factory=lambda: os.environ.get("SIDEKICK_AGENT_MODEL_NAME")
        or os.environ.get("VLLM_MODEL")
        or "Qwen3.5-122B-A10B"
    )
    # Local servers are keyless; the OpenAI wire format still wants a non-empty bearer, so
    # default to the conventional "EMPTY" sentinel. Set VLLM_API_KEY only if you front the
    # server with an auth proxy.
    vllm_api_key: str | None = field(
        default_factory=lambda: os.environ.get("VLLM_API_KEY") or "EMPTY"
    )
    # Sampling temperature for the agentic loop. Lower is steadier for merge/refactor work;
    # override with VLLM_TEMPERATURE.
    vllm_temperature: float = field(
        default_factory=lambda: float(os.environ.get("VLLM_TEMPERATURE", "0.2"))
    )
    # Model for spawned agents/planner; None inherits the Claude Code default.
    agent_model: str | None = field(default_factory=lambda: os.environ.get("SIDEKICK_AGENT_MODEL") or None)
    planner_model: str | None = field(
        default_factory=lambda: os.environ.get("SIDEKICK_PLANNER_MODEL") or None
    )
    concurrency: int = field(default_factory=lambda: int(os.environ.get("SIDEKICK_CONCURRENCY", "3")))
    approval: str = field(
        default_factory=lambda: os.environ.get("SIDEKICK_APPROVAL", APPROVAL_ACCEPT_EDITS_ALLOWLIST)
    )
    # Per-agent budget guards.
    agent_max_turns: int = field(default_factory=lambda: int(os.environ.get("SIDEKICK_MAX_TURNS", "40")))
    agent_timeout_s: int = field(default_factory=lambda: int(os.environ.get("SIDEKICK_AGENT_TIMEOUT", "1800")))
    retry_failed: int = field(default_factory=lambda: int(os.environ.get("SIDEKICK_RETRY", "1")))
    # Context-budget limits (Raschka #4).
    clip_tool_output: int = 4000
    # VSCode integration: None = auto-detect (`code` CLI present), True/False to force.
    vscode: bool | None = field(
        default_factory=lambda: {"1": True, "0": False}.get(os.environ.get("SIDEKICK_VSCODE", ""), None)
    )
    # Where sidekick stores run state, worktrees, metrics, memory, skills.
    state_dirname: str = field(
        default_factory=lambda: os.environ.get("SIDEKICK_STATE_DIR")
        or ".sidekick-selfhosted"
    )

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root).resolve()
        if self.concurrency < 1:
            self.concurrency = 1

    @property
    def state_dir(self) -> Path:
        return self.repo_root / self.state_dirname

    @property
    def worktrees_dir(self) -> Path:
        return self.state_dir / "worktrees"

    @property
    def runs_dir(self) -> Path:
        return self.state_dir / "runs"

    @property
    def memory_dir(self) -> Path:
        return self.state_dir / "memory"

    @property
    def skills_dir(self) -> Path:
        return self.state_dir / "skills"

    @property
    def metrics_path(self) -> Path:
        return self.state_dir / "metrics.jsonl"

    def ensure_dirs(self) -> None:
        for d in (self.state_dir, self.worktrees_dir, self.runs_dir, self.memory_dir, self.skills_dir):
            d.mkdir(parents=True, exist_ok=True)
