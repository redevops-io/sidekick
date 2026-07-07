from sidekick import llm_session as K
from sidekick.approval import ApprovalPolicy
from sidekick.config import APPROVAL_ACCEPT_EDITS_ALLOWLIST, APPROVAL_BYPASS, APPROVAL_EDITS_NO_BASH, Config
from sidekick.providers import CLAUDE, is_claude, resolve


def _policy(level):
    return ApprovalPolicy(level)


# --- tool loop internals (provider-agnostic) ---------------------------------


def test_tool_specs_include_bash_only_when_allowed():
    names = {t["function"]["name"] for t in K._tool_specs(_policy(APPROVAL_ACCEPT_EDITS_ALLOWLIST))}
    assert {"read_file", "write_file", "edit_file", "list_dir", "finish"} <= names
    assert "run_bash" in names
    no_bash = {t["function"]["name"] for t in K._tool_specs(_policy(APPROVAL_EDITS_NO_BASH))}
    assert "run_bash" not in no_bash


def test_bash_prefixes_derived():
    pres = K._bash_prefixes()
    assert "uv " in pres and "pytest " in pres and "git status" in pres


def test_write_read_edit_roundtrip(tmp_path):
    pol = _policy(APPROVAL_ACCEPT_EDITS_ALLOWLIST)
    assert "wrote" in K._exec_tool("write_file", {"path": "m.py", "content": "x = 1\n"}, tmp_path, pol)
    assert "x = 1" in K._exec_tool("read_file", {"path": "m.py"}, tmp_path, pol)
    assert "edited" in K._exec_tool("edit_file", {"path": "m.py", "old": "1", "new": "2"}, tmp_path, pol)
    assert (tmp_path / "m.py").read_text() == "x = 2\n"


def test_edit_missing_old_returns_error(tmp_path):
    pol = _policy(APPROVAL_ACCEPT_EDITS_ALLOWLIST)
    K._exec_tool("write_file", {"path": "m.py", "content": "a\n"}, tmp_path, pol)
    out = K._exec_tool("edit_file", {"path": "m.py", "old": "zzz", "new": "b"}, tmp_path, pol)
    assert "not found" in out


def test_path_escape_blocked(tmp_path):
    pol = _policy(APPROVAL_ACCEPT_EDITS_ALLOWLIST)
    out = K._exec_tool("read_file", {"path": "../../etc/passwd"}, tmp_path, pol)
    assert "escapes" in out.lower() or "error" in out.lower()


def test_bash_allowlist_blocks_unlisted(tmp_path):
    pol = _policy(APPROVAL_ACCEPT_EDITS_ALLOWLIST)
    blocked = K._exec_tool("run_bash", {"command": "rm -rf /"}, tmp_path, pol)
    assert "allowlist" in blocked
    ok = K._exec_tool("run_bash", {"command": "ls ."}, tmp_path, pol)
    assert "exit 0" in ok


def test_bash_disabled_policy(tmp_path):
    out = K._exec_tool("run_bash", {"command": "ls"}, tmp_path, _policy(APPROVAL_EDITS_NO_BASH))
    assert "disabled" in out


def test_bypass_allows_any_bash(tmp_path):
    out = K._exec_tool("run_bash", {"command": "echo hello"}, tmp_path, _policy(APPROVAL_BYPASS))
    assert "exit 0" in out and "hello" in out


def test_accum_usage():
    from sidekick.agent_session import AgentResult

    r = AgentResult(name="x")
    K._accum_usage(r, {"prompt_tokens": 10, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 3}})
    assert r.tokens["input"] == 10 and r.tokens["output"] == 5 and r.tokens["cache_read"] == 3


# --- provider presets → LiteLLM settings -------------------------------------


def test_resolve_hosted_presets():
    assert resolve("openai").model == "openai/gpt-5-codex"
    assert resolve("gemini").model == "gemini/gemini-2.5-pro"
    assert resolve("grok").model.startswith("xai/")
    assert resolve("kimi").temperature == 1.0  # reasoning model → fixed temperature


def test_resolve_local_defaults_offline_no_key(monkeypatch):
    for e in ("OPENAI_API_KEY", "SIDEKICK_API_KEY"):
        monkeypatch.delenv(e, raising=False)
    s = resolve("local-cpu")
    assert s.api_base and s.api_base.endswith("/v1")
    assert s.api_key == "sk-local"  # local sentinel, no real key required
    assert s.reachable_check is True
    # local-metal shares the offline shape.
    assert resolve("local-metal").reachable_check is True


def test_resolve_cuda_preset(monkeypatch):
    monkeypatch.delenv("SIDEKICK_API_KEY", raising=False)
    s = resolve("cuda")
    # NVIDIA lane: local OpenAI-compatible vLLM/llama.cpp server on :8000, offline, no key.
    assert s.api_base == "http://localhost:8000/v1"
    assert s.model.startswith("openai/")
    assert s.api_key == "EMPTY" and s.reachable_check is True


def test_resolve_overrides_win():
    s = resolve("openai", model="openai/gpt-4o", api_base="http://x/v1", api_key="k", temperature=0.5)
    assert (s.model, s.api_base, s.api_key, s.temperature) == ("openai/gpt-4o", "http://x/v1", "k", 0.5)


def test_resolve_key_from_env(monkeypatch):
    monkeypatch.delenv("SIDEKICK_API_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    assert resolve("openai").api_key == "sk-from-env"


def test_resolve_unknown_provider_is_raw_model():
    s = resolve("openrouter/anthropic/claude-3.5-sonnet")
    assert s.model == "openrouter/anthropic/claude-3.5-sonnet"


def test_is_claude():
    assert is_claude(CLAUDE) and not is_claude("openai")


def test_config_defaults_to_local_cpu(monkeypatch, tmp_path):
    monkeypatch.delenv("SIDEKICK_PROVIDER", raising=False)
    cfg = Config(repo_root=tmp_path)
    assert cfg.provider == "local-cpu"
    assert cfg.llm().model.startswith("openai/")


# --- mocked LiteLLM completion loop ------------------------------------------


class _FakeResp:
    """Minimal stand-in for a LiteLLM ModelResponse (supports model_dump())."""

    def __init__(self, payload):
        self._payload = payload

    def model_dump(self):
        return self._payload


def test_completion_calls_litellm_and_normalizes(monkeypatch, tmp_path):
    captured = {}

    def fake_completion(**kwargs):
        captured.update(kwargs)
        return _FakeResp({
            "choices": [{"message": {"content": "done", "tool_calls": []}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 2},
        })

    import litellm

    monkeypatch.setattr(litellm, "completion", fake_completion)
    s = resolve("local-cpu")
    data = K._completion(s, [{"role": "user", "content": "hi"}], None, 30)
    assert data["choices"][0]["message"]["content"] == "done"
    # settings threaded into the call
    assert captured["model"] == s.model
    assert captured["api_base"] == s.api_base
    assert captured["temperature"] == s.temperature


def test_run_loop_finishes_on_finish_tool(monkeypatch, tmp_path):
    """A two-step conversation: model calls write_file then finish; loop should end
    successful with one edit recorded."""
    steps = [
        _FakeResp({"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "1", "function": {"name": "write_file",
                                     "arguments": '{"path": "a.txt", "content": "hello"}'}}]}}],
                   "usage": {"prompt_tokens": 3, "completion_tokens": 1}}),
        _FakeResp({"choices": [{"message": {"content": None, "tool_calls": [
            {"id": "2", "function": {"name": "finish", "arguments": "{}"}}]}}],
                   "usage": {"prompt_tokens": 2, "completion_tokens": 1}}),
    ]

    def fake_completion(**kwargs):
        return steps.pop(0)

    import litellm

    monkeypatch.setattr(litellm, "completion", fake_completion)
    cfg = Config(repo_root=tmp_path, provider="local-cpu")
    res = K._run_llm_sync(cfg, _policy(APPROVAL_ACCEPT_EDITS_ALLOWLIST), "t", "do it", tmp_path, None, "sys")
    assert res.success is True
    assert res.edits == 1
    assert (tmp_path / "a.txt").read_text() == "hello"


def test_completion_wraps_errors(monkeypatch, tmp_path):
    def boom(**kwargs):
        raise RuntimeError("provider exploded")

    import litellm

    monkeypatch.setattr(litellm, "completion", boom)
    cfg = Config(repo_root=tmp_path, provider="local-cpu")
    res = K._run_llm_sync(cfg, _policy(APPROVAL_ACCEPT_EDITS_ALLOWLIST), "t", "x", tmp_path, None, None)
    assert res.success is False and "provider exploded" in res.error
