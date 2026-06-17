from loopie import kimi_session as K
from loopie.approval import ApprovalPolicy
from loopie.config import APPROVAL_ACCEPT_EDITS_ALLOWLIST, APPROVAL_BYPASS, APPROVAL_EDITS_NO_BASH


def _policy(level):
    return ApprovalPolicy(level)


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
    from loopie.agent_session import AgentResult

    r = AgentResult(name="x")
    K._accum_usage(r, {"prompt_tokens": 10, "completion_tokens": 5, "prompt_tokens_details": {"cached_tokens": 3}})
    assert r.tokens["input"] == 10 and r.tokens["output"] == 5 and r.tokens["cache_read"] == 3
