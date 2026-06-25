from sidekick.orchestrator import topo_waves
from sidekick.planner import Plan, Subtask, _extract_json, _parse_plan


def test_extract_json_plain():
    assert _extract_json('{"a": 1}') == {"a": 1}


def test_extract_json_fenced():
    assert _extract_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_json_embedded():
    assert _extract_json('blah blah {"a": 1} trailing') == {"a": 1}


def test_parse_plan_drops_unknown_deps():
    data = {
        "subtasks": [
            {"id": "a", "description": "do a", "deps": ["ghost"]},
            {"id": "b", "description": "do b", "deps": ["a"]},
        ]
    }
    plan = _parse_plan("t", data)
    a = next(s for s in plan.subtasks if s.id == "a")
    b = next(s for s in plan.subtasks if s.id == "b")
    assert a.deps == []  # ghost removed
    assert b.deps == ["a"]


def test_parse_plan_empty_falls_back():
    plan = _parse_plan("the task", {"subtasks": []})
    assert len(plan.subtasks) == 1 and plan.subtasks[0].description == "the task"


def test_topo_waves_orders_by_deps():
    subs = [
        Subtask(id="a", title="", description=""),
        Subtask(id="b", title="", description="", deps=["a"]),
        Subtask(id="c", title="", description="", deps=["a"]),
        Subtask(id="d", title="", description="", deps=["b", "c"]),
    ]
    waves = topo_waves(subs)
    ids = [[s.id for s in w] for w in waves]
    assert ids[0] == ["a"]
    assert set(ids[1]) == {"b", "c"}
    assert ids[2] == ["d"]


def test_topo_waves_breaks_cycle():
    subs = [
        Subtask(id="a", title="", description="", deps=["b"]),
        Subtask(id="b", title="", description="", deps=["a"]),
    ]
    waves = topo_waves(subs)
    assert sum(len(w) for w in waves) == 2  # all scheduled despite cycle


def test_plan_roundtrip_dict():
    plan = Plan(task="t", subtasks=[Subtask(id="a", title="A", description="d", acceptance_checks=["true"])])
    d = plan.to_dict()
    assert d["subtasks"][0]["acceptance_checks"] == ["true"]


def test_parse_plan_background_flag_roundtrip():
    data = {
        "subtasks": [
            {"id": "a", "description": "fg work"},
            {"id": "b", "description": "bg work", "background": True},
        ]
    }
    plan = _parse_plan("t", data)
    flags = {s.id: s.background for s in plan.subtasks}
    assert flags == {"a": False, "b": True}
    # Survives serialization.
    assert {s["id"]: s["background"] for s in plan.to_dict()["subtasks"]} == {"a": False, "b": True}


def test_background_subtasks_excluded_from_foreground_waves():
    # Background subtasks are scheduled separately, not inside the dependency waves.
    subs = [
        Subtask(id="a", title="", description=""),
        Subtask(id="bg", title="", description="", background=True),
    ]
    foreground = [s for s in subs if not s.background]
    ids = [[s.id for s in w] for w in topo_waves(foreground)]
    assert ids == [["a"]]
