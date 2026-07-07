"""LLM provider presets → LiteLLM settings.

`main` has exactly two backend kinds:
  * `claude` — the native Claude Code binary (a full agentic harness; not routed through
    LiteLLM). Selected by `provider == "claude"`.
  * everything else — sidekick's own agentic tool loop (`llm_session.py`) talking to any
    model through **LiteLLM** (`litellm.completion`). A "provider" here is just a preset
    that expands to a LiteLLM model string + optional api_base / api-key env / temperature.

Presets are conveniences; `--model` / `--api-base` / `--api-key` / `--temperature` (or the
`SIDEKICK_MODEL` / `SIDEKICK_API_BASE` / `SIDEKICK_API_KEY` / `SIDEKICK_TEMPERATURE` env
vars) override any field. An unknown provider string is treated as a raw LiteLLM model
(e.g. `--provider openrouter/anthropic/claude-3.5-sonnet`), so power users aren't boxed in.

Local backends (fully offline, no key): `local-cpu` and `local-metal` point at a local
OpenAI-compatible server (llama.cpp / vLLM / LM Studio / MLX) — on macOS build llama.cpp
with Metal (`-DGGML_METAL=on`) or use Ollama/MLX for GPU; on Linux use a CPU or ROCm/CUDA
build. Either way the wire protocol is identical, so sidekick's loop is unchanged.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

CLAUDE = "claude"  # native Claude Code CLI backend (not LiteLLM)

# Default when nothing is set — fully offline, no API key required.
DEFAULT_PROVIDER = "local-cpu"

_LOCAL_BASE = "http://localhost:8080/v1"


@dataclass(frozen=True)
class Preset:
    model: str
    api_base: str | None = None
    # Env vars checked (in order) for an API key when none is passed explicitly.
    key_envs: tuple[str, ...] = ()
    # Fallback key when no env var is set — local servers want a non-empty bearer.
    default_key: str | None = None
    temperature: float | None = None
    # Whether to liveness-probe api_base before a run (local servers may be down).
    reachable_check: bool = False


PRESETS: dict[str, Preset] = {
    # Hosted providers — LiteLLM handles the base URL + auth from the model prefix.
    "anthropic": Preset("anthropic/claude-sonnet-4-5", key_envs=("ANTHROPIC_API_KEY",)),
    "openai": Preset("openai/gpt-5-codex", key_envs=("OPENAI_API_KEY",)),
    "kimi": Preset(
        "moonshot/kimi-k2.7-code",
        key_envs=("MOONSHOT_API_KEY", "KIMI_AGENT_API_KEY", "KIMI_API_KEY"),
        temperature=1.0,  # kimi-k2.x reasoning models only accept the default temperature
    ),
    "gemini": Preset("gemini/gemini-2.5-pro", key_envs=("GEMINI_API_KEY", "GOOGLE_API_KEY")),
    "grok": Preset("xai/grok-4", key_envs=("XAI_API_KEY", "GROK_API_KEY")),
    # Local, offline backends via an OpenAI-compatible server. `local-model` is a placeholder
    # name — llama.cpp serves whatever GGUF is loaded regardless of the model field; for vLLM
    # pass the served name with --model.
    "local-cpu": Preset(
        "openai/local-model", api_base=_LOCAL_BASE, default_key="sk-local",
        temperature=0.2, reachable_check=True,
    ),
    "local-metal": Preset(
        "openai/local-model", api_base=_LOCAL_BASE, default_key="sk-local",
        temperature=0.2, reachable_check=True,
    ),
    # Back-compat alias for the old vLLM/llama.cpp `selfhosted` setup (evo-x2 on :8000).
    "selfhosted": Preset(
        "openai/local-model", api_base="http://localhost:8000/v1", default_key="EMPTY",
        temperature=0.2, reachable_check=True,
    ),
    "ollama": Preset("ollama_chat/qwen2.5-coder:7b", temperature=0.2),
}


@dataclass
class LLMSettings:
    provider: str
    model: str
    api_base: str | None
    api_key: str | None
    temperature: float | None
    reachable_check: bool


def is_claude(provider: str) -> bool:
    return provider == CLAUDE


def resolve(
    provider: str,
    *,
    model: str | None = None,
    api_base: str | None = None,
    api_key: str | None = None,
    temperature: float | None = None,
) -> LLMSettings:
    """Expand a provider (+ overrides) into concrete LiteLLM settings.

    Overrides win over the preset; an unknown provider is treated as a raw LiteLLM model
    string. The API key is taken from the explicit override, then the preset's key envs,
    then the preset's default (local sentinel)."""
    preset = PRESETS.get(provider) or Preset(model=provider)
    resolved_model = model or preset.model
    resolved_base = api_base or preset.api_base
    key = api_key
    if not key:
        for env in preset.key_envs:
            val = os.environ.get(env)
            if val:
                key = val
                break
    if not key:
        key = preset.default_key
    temp = temperature if temperature is not None else preset.temperature
    return LLMSettings(
        provider=provider,
        model=resolved_model,
        api_base=resolved_base,
        api_key=key,
        temperature=temp,
        reachable_check=preset.reachable_check and bool(resolved_base),
    )
