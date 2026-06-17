import dataclasses

from loopie import metrics as M


def _sub(**kw):
    base = dict(
        run_id="r", subtask_id="s", success=True, accepted=True, first_attempt=True,
        merged=True, merge_attempted=True, wall_ms=3000, ttft_ms=500,
        time_to_first_edit_ms=3000, num_turns=2, tokens_total=1000, cost_usd=0.01,
        cache_hit_ratio=0.7,
    )
    base.update(kw)
    return dataclasses.asdict(M.SubtaskRecord(**base))


def _run(mode, wall_ms, agent_ms_sum, concurrency):
    return dataclasses.asdict(
        M.RunRecord(run_id="r", task="t", mode=mode, concurrency=concurrency,
                    n_subtasks=3, wall_ms=wall_ms, agent_ms_sum=agent_ms_sum, human_wait_ms=0)
    )


def test_speedup_and_gates_pass():
    records = [
        _run("serial", 9000, 9000, 1),
        _run("orchestrated", 3200, 9000, 3),  # crit-path 3000; overhead ~6% (S1 < 8%)
        _sub(subtask_id="a"),
        _sub(subtask_id="b"),
        _sub(subtask_id="c"),
    ]
    objs = {o.id: o for o in M.compute(records)}
    assert objs["S2"].value >= 2.2 and objs["S2"].passed
    assert objs["S4"].value == 0 and objs["S4"].passed
    assert objs["A1"].value == 100.0 and objs["A1"].passed
    assert abs(objs["E2"].value - 70.0) < 1e-6 and objs["E2"].passed
    assert M.gate(M.compute(records))


def test_failing_acceptance_misses_a1():
    records = [_sub(accepted=False, first_attempt=False), _sub(accepted=True)]
    objs = {o.id: o for o in M.compute(records)}
    assert objs["A1"].value == 50.0 and objs["A1"].passed is False
    assert not M.gate(M.compute(records))


def test_merge_conflict_rate():
    records = [_sub(merged=False, merge_attempted=True), _sub(merged=True, merge_attempted=True)]
    objs = {o.id: o for o in M.compute(records)}
    assert objs["A3"].value == 50.0  # one of two attempted merges failed


def test_no_data_is_informational():
    objs = {o.id: o for o in M.compute([])}
    assert objs["S2"].value is None and objs["S2"].passed is None
    assert M.gate(M.compute([]))  # no data never fails the gate
