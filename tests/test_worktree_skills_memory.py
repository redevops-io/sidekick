import subprocess

from loopie.memory import SessionMemory
from loopie.repo_context import gather
from loopie.skills import Skill, SkillStore
from loopie.worktree import WorktreeManager


def _init_repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "README.md").write_text("# demo\nhello")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)


def test_worktree_create_commit_merge(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    mgr = WorktreeManager(repo, tmp_path / "wts")
    wt = mgr.create("alpha")
    assert wt.path.exists()
    (wt.path / "new.py").write_text("x = 1\n")
    assert mgr.has_changes(wt)
    assert mgr.commit_all(wt, "add new") is True
    assert mgr.merge_clean(wt) is True
    assert (repo / "new.py").exists()  # merged into base branch
    wt.remove()
    assert not wt.path.exists()


def test_worktree_excludes_bytecode_and_merges_disjoint(tmp_path):
    # Two agents touch disjoint files but each also leaves __pycache__/*.pyc (as the
    # `python3 -c import` acceptance checks do). Bytecode must not enter branches or
    # block the second merge.
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    mgr = WorktreeManager(repo, tmp_path / "wts")
    for sid in ("alpha", "beta"):
        wt = mgr.create(sid)
        (wt.path / f"{sid}.py").write_text(f"V = '{sid}'\n")
        cache = wt.path / "__pycache__"
        cache.mkdir()
        (cache / f"{sid}.cpython-314.pyc").write_bytes(b"\x00\x01bytecode")
        assert mgr.commit_all(wt, f"add {sid}") is True
        committed = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", wt.branch],
            cwd=repo, capture_output=True, text=True,
        ).stdout
        assert f"{sid}.py" in committed
        assert ".pyc" not in committed  # bytecode excluded
        assert mgr.merge_clean(wt) is True  # both merge cleanly despite pyc on disk
    assert (repo / "alpha.py").exists() and (repo / "beta.py").exists()


def test_worktree_merge_conflict_returns_false(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    mgr = WorktreeManager(repo, tmp_path / "wts")
    a = mgr.create("a")
    b = mgr.create("b")
    (a.path / "README.md").write_text("# demo\nAAA")
    (b.path / "README.md").write_text("# demo\nBBB")
    mgr.commit_all(a, "a")
    mgr.commit_all(b, "b")
    assert mgr.merge_clean(a) is True
    assert mgr.merge_clean(b) is False  # conflicts with a's change


def test_skills_save_and_recall(tmp_path):
    store = SkillStore(tmp_path / "skills")
    store.save(Skill(name="add validation", trigger="validate user input", approach="guard clauses"))
    hits = store.recall("please validate the input fields")
    assert hits and hits[0].name == "add validation"
    assert store.recall("totally unrelated quantum") == []


def test_session_memory_roundtrip(tmp_path):
    mem = SessionMemory(tmp_path / "run", task="do x")
    mem.append_transcript("agent", "did a thing")
    mem.working.note("note1")
    mem.working.done_subtasks.append("a")
    mem.save_working()
    reloaded = SessionMemory(tmp_path / "run")
    assert reloaded.working.task == "do x"
    assert reloaded.working.done_subtasks == ["a"]
    assert len(reloaded.load_transcript()) == 1


def test_repo_context_render(tmp_path):
    _init_repo(tmp_path)
    ctx = gather(tmp_path)
    assert ctx.is_git
    summary = ctx.render()
    assert "Workspace" in summary and "README.md" in summary
