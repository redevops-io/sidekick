"""Sidekick UI product surface — argv translation to the ui-agent engine + graceful skip.

Browser-free: `sidekick.ui._invoke` is patched, so these assert the CLI wires the right engine command
and honours the deterministic-proof contract (test requires --expect) without a browser or the engine."""
from __future__ import annotations

from unittest import mock

import pytest

from sidekick import cli, ui


def _capture_engine_argv(cli_args):
    """Run the sidekick CLI with the engine subprocess mocked; return the engine argv it built."""
    seen = {}

    def fake_invoke(argv, *, json_out=True):
        seen["argv"] = argv
        return {"returncode": 0, "stdout": "ok", "stderr": "", "result": {}}

    with mock.patch.object(ui, "_invoke", side_effect=fake_invoke):
        rc = cli.main(cli_args)
    return rc, seen.get("argv")


def test_test_maps_to_journey_and_requires_expect():
    rc, argv = _capture_engine_argv(
        ["test", "https://app.x", "Sign up and reach the dashboard", "--expect", "url_contains:/dashboard"])
    assert rc == 0
    assert argv[0] == "journey"
    assert argv[argv.index("--url") + 1] == "https://app.x"
    assert argv[argv.index("--success") + 1] == "url_contains:/dashboard"
    # goal becomes the default step
    assert argv[argv.index("--step") + 1] == "Sign up and reach the dashboard"


def test_test_requires_expect():
    with pytest.raises(SystemExit):
        cli.main(["test", "https://app.x", "do a thing"])


def test_audit_url_type_and_all_regressions():
    _, argv = _capture_engine_argv(["audit", "https://x", "--type", "Landing/Marketing"])
    assert argv == ["audit-ui", "--url", "https://x", "--type", "Landing/Marketing"]
    _, argv = _capture_engine_argv(["audit", "--all", "--assert-regressions", "r.json"])
    assert argv == ["audit-ui", "--all", "--assert-regressions", "r.json"]


def test_benchmark_maps_through():
    _, argv = _capture_engine_argv(["benchmark", "--capability", "cost_audit", "--target", "redevops"])
    assert argv == ["benchmark", "--capability", "cost_audit", "--target", "redevops"]


def test_missing_engine_skips_gracefully():
    with mock.patch.object(ui, "_invoke", side_effect=ui.EngineUnavailable("no engine")):
        rc = cli.main(["audit", "https://x"])
    assert rc == 0  # self-skip, not a hard failure


def test_video_dir_and_engine_reach_the_engine():
    _, argv = _capture_engine_argv(["test", "https://app.x", "g", "--expect", "selector:h1", "--engine", "playwright", "--video-dir", "/v"])
    assert argv[-2:] == ["--video-dir", "/v"] and argv[argv.index("--engine") + 1] == "playwright"
    _, a = _capture_engine_argv(["audit", "https://x", "--video-dir", "/v"])
    assert a == ["audit-ui", "--url", "https://x", "--video-dir", "/v"]
    _, d = _capture_engine_argv(["deploy-audit", "--video-dir", "/v"])
    assert d[-2:] == ["--video-dir", "/v"]


def test_no_video_flag_changes_nothing():
    _, argv = _capture_engine_argv(["audit", "https://x"])
    assert "--video-dir" not in argv


def test_capture_dir_reaches_the_engine_for_every_ui_mode():
    _, t = _capture_engine_argv(["test", "https://app.x", "g", "--expect", "selector:h1", "--capture-dir", "/c"])
    _, a = _capture_engine_argv(["audit", "https://x", "--capture-dir", "/c"])
    _, d = _capture_engine_argv(["deploy-audit", "--capture-dir", "/c"])
    assert all(x[-2:] == ["--capture-dir", "/c"] for x in (t, a, d))
