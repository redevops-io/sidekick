from pathlib import Path

from sidekick.agent_session import build_command
from sidekick.approval import ApprovalPolicy
from sidekick.config import (
    APPROVAL_ACCEPT_EDITS_ALLOWLIST,
    APPROVAL_BYPASS,
    APPROVAL_EDITS_NO_BASH,
    Config,
)


def test_allowlist_policy_includes_edits_and_scoped_bash():
    p = ApprovalPolicy(APPROVAL_ACCEPT_EDITS_ALLOWLIST)
    tools = p.allowed_tools()
    assert "Edit" in tools and "Write" in tools
    assert any(t.startswith("Bash(") for t in tools)
    assert p.permission_mode == "acceptEdits"
    assert not p.requires_dangerous_flag


def test_bypass_policy():
    p = ApprovalPolicy(APPROVAL_BYPASS)
    assert p.permission_mode == "bypassPermissions"
    assert p.requires_dangerous_flag
    assert p.allowed_tools() == []


def test_edits_no_bash_disallows_bash():
    p = ApprovalPolicy(APPROVAL_EDITS_NO_BASH)
    assert "Bash" in p.disallowed_tools()
    assert not any(t.startswith("Bash(") for t in p.allowed_tools())
    assert not p.can_run_bash


def test_build_command_shape(tmp_path):
    cfg = Config(repo_root=tmp_path)
    cfg.claude_bin = "/bin/claude"
    p = ApprovalPolicy(APPROVAL_ACCEPT_EDITS_ALLOWLIST)
    cmd = build_command(cfg, p, "do it", "sid-123", "claude-haiku-4-5-20251001", "SYS")
    assert cmd[0] == "/bin/claude"
    assert "-p" in cmd and "do it" in cmd
    assert "--output-format" in cmd and "stream-json" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "acceptEdits"
    assert cmd[cmd.index("--session-id") + 1] == "sid-123"
    assert cmd[cmd.index("--append-system-prompt") + 1] == "SYS"
    assert cmd[cmd.index("--model") + 1] == "claude-haiku-4-5-20251001"


def test_build_command_bypass_adds_dangerous_flag(tmp_path):
    cfg = Config(repo_root=tmp_path)
    p = ApprovalPolicy(APPROVAL_BYPASS)
    cmd = build_command(cfg, p, "x", "sid", None)
    assert "--allow-dangerously-skip-permissions" in cmd
    assert "--model" not in cmd  # None model omitted
    _ = Path  # keep import used
