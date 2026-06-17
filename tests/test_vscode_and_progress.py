from sidekick import vscode
from sidekick.dashboard import Dashboard


def test_open_file_cmd_reuses_window():
    cmd = vscode.open_file_cmd("/tmp/x.md", reuse=True)
    assert cmd[0] == "code" and "-r" in cmd and cmd[-1] == "/tmp/x.md"
    cmd2 = vscode.open_file_cmd("/tmp/x.md", reuse=False)
    assert "-r" not in cmd2


def test_open_diff_cmd():
    cmd = vscode.open_diff_cmd("a.py", "b.py")
    assert cmd[:2] == ["code", "--diff"] and cmd[2:] == ["a.py", "b.py"]


def test_dashboard_writes_markdown_progress(tmp_path):
    p = tmp_path / "progress.md"
    dash = Dashboard("sidekick · test", use_rich=False, progress_path=p, concurrency=3)
    dash.register("alpha")
    dash.register("beta")
    dash.set_status("alpha", "running", "Write alpha.py")
    dash.set_status("alpha", "done")
    dash.finalize("## Result\n\n**1/2 accepted**")
    md = p.read_text(encoding="utf-8")
    assert "# sidekick · test" in md
    assert "| `alpha` |" in md and "✅ done" in md
    assert "concurrency 3" in md
    assert "## Result" in md


def test_markdown_escapes_pipes(tmp_path):
    dash = Dashboard("t", use_rich=False, progress_path=tmp_path / "p.md")
    dash.register("a")
    dash.set_status("a", "running", "Bash: a | b")
    md = dash.render_markdown()
    # The action's pipe must be escaped so it doesn't break the table column.
    assert "a \\| b" in md
