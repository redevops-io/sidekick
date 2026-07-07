"""Configuration and path resolution for sidekick."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .providers import DEFAULT_PROVIDER, LLMSettings, resolve


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
    # Agent execution backend, selected by `--provider` / SIDEKICK_PROVIDER:
    #   "claude"                → native Claude Code binary (a full agentic harness)
    #   any other preset/string → sidekick's own tool loop via LiteLLM (see providers.py):
    #                             openai | anthropic | kimi | gemini | grok |
    #                             local-cpu | local-metal | selfhosted | ollama |
    #                             or a raw LiteLLM model string.
    # Default is "local-cpu" — fully offline, no API key required.
    provider: str = field(default_factory=lambda: os.environ.get("SIDEKICK_PROVIDER") or DEFAULT_PROVIDER)
    # Generic LiteLLM overrides (None → fall back to the provider preset). VLLM_BASE_URL is
    # honored for api_base as a back-compat convenience for the old self-hosted setup.
    model: str | None = field(default_factory=lambda: os.environ.get("SIDEKICK_MODEL") or None)
    api_base: str | None = field(
        default_factory=lambda: os.environ.get("SIDEKICK_API_BASE") or os.environ.get("VLLM_BASE_URL") or None
    )
    api_key: str | None = field(default_factory=lambda: os.environ.get("SIDEKICK_API_KEY") or None)
    temperature: float | None = field(
        default_factory=lambda: float(os.environ["SIDEKICK_TEMPERATURE"])
        if os.environ.get("SIDEKICK_TEMPERATURE")
        else None
    )
    # Model for the native Claude Code path; None inherits the Claude Code default.
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
        default_factory=lambda: os.environ.get("SIDEKICK_STATE_DIR") or ".sidekick"
    )

    def __post_init__(self) -> None:
        self.repo_root = Path(self.repo_root).resolve()
        if self.concurrency < 1:
            self.concurrency = 1

    def llm(self) -> LLMSettings:
        """Resolve the effective LiteLLM settings for the configured provider."""
        return resolve(
            self.provider,
            model=self.model,
            api_base=self.api_base,
            api_key=self.api_key,
            temperature=self.temperature,
        )

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
