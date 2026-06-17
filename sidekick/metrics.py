"""Measurable objectives (see OBJECTIVES.md).

Records per-subtask and per-run records to metrics.jsonl and computes the objective table
(S1-S4 speed, A1-A4 accuracy, E1-E2 efficiency). `bench` uses gate() to fail on regress.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class SubtaskRecord:
    run_id: str
    subtask_id: str
    success: bool
    accepted: bool  # acceptance checks passed
    first_attempt: bool  # passed without a retry
    merged: bool
    merge_attempted: bool
    wall_ms: int
    ttft_ms: int | None
    time_to_first_edit_ms: int | None
    num_turns: int | None
    tokens_total: int
    cost_usd: float
    cache_hit_ratio: float
    kind: str = "subtask"


@dataclass
class RunRecord:
    run_id: str
    task: str
    mode: str  # "orchestrated" | "serial"
    concurrency: int
    n_subtasks: int
    wall_ms: int
    agent_ms_sum: int  # sum of per-agent wall times (serial-equivalent compute)
    human_wait_ms: int
    kind: str = "run"
    ts: float = field(default_factory=time.time)


def append(path: Path, record) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(asdict(record)) + "\n")


def load(path: Path) -> list[dict]:
    if not Path(path).exists():
        return []
    out = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


@dataclass
class Objective:
    id: str
    name: str
    value: float | None
    target: str
    unit: str
    passed: bool | None  # None = informational / no data


def _pct(n: int, d: int) -> float | None:
    return (100.0 * n / d) if d else None


def compute(records: list[dict]) -> list[Objective]:
    runs = [r for r in records if r.get("kind") == "run"]
    subs = [r for r in records if r.get("kind") == "subtask"]
    orchestrated = [r for r in runs if r.get("mode") == "orchestrated"]
    serial = [r for r in runs if r.get("mode") == "serial"]

    objs: list[Objective] = []

    # S1 orchestration overhead: wall time beyond the unavoidable critical path (the
    # single longest agent in the run). This isolates sidekick's own cost — scheduling,
    # worktree setup, acceptance checks, merges — from agent compute. Lower is better.
    s1 = None
    if orchestrated:
        ratios = []
        for r in orchestrated:
            wall = r.get("wall_ms", 0)
            run_subs = [s for s in subs if s.get("run_id") == r.get("run_id")]
            crit = max((s.get("wall_ms", 0) for s in run_subs), default=0)
            if wall > 0 and crit > 0:
                ratios.append(max(0.0, (wall - crit)) / wall)
        s1 = (100.0 * sum(ratios) / len(ratios)) if ratios else None
    objs.append(Objective("S1", "Orchestration overhead", s1, "< 8%", "%", None if s1 is None else s1 < 8))

    # S2 parallel speedup: median serial wall / median orchestrated wall.
    s2 = None
    if serial and orchestrated:
        sm = _median([r["wall_ms"] for r in serial])
        om = _median([r["wall_ms"] for r in orchestrated])
        if om:
            s2 = sm / om
    objs.append(Objective("S2", "Parallel speedup", s2, ">= 2.2x", "x", None if s2 is None else s2 >= 2.2))

    # S3 time-to-first-edit: median across subtasks (seconds).
    edits = [s["time_to_first_edit_ms"] for s in subs if s.get("time_to_first_edit_ms")]
    s3 = (_median(edits) / 1000.0) if edits else None
    objs.append(Objective("S3", "Time-to-first-edit", s3, "< 20s", "s", None if s3 is None else s3 < 20))

    # S4 human-wait: total seconds waiting on manual approval.
    s4 = sum(r.get("human_wait_ms", 0) for r in runs) / 1000.0 if runs else None
    objs.append(Objective("S4", "Human-wait time", s4, "= 0", "s", None if s4 is None else s4 == 0))

    # A1 acceptance pass rate.
    a1 = _pct(sum(1 for s in subs if s.get("accepted")), len(subs))
    objs.append(Objective("A1", "Acceptance pass rate", a1, ">= 90%", "%", None if a1 is None else a1 >= 90))

    # A2 first-attempt success.
    a2 = _pct(sum(1 for s in subs if s.get("first_attempt") and s.get("accepted")), len(subs))
    objs.append(Objective("A2", "First-attempt success", a2, ">= 70%", "%", None if a2 is None else a2 >= 70))

    # A3 merge-conflict rate (of attempted merges).
    attempted = [s for s in subs if s.get("merge_attempted")]
    a3 = _pct(sum(1 for s in attempted if not s.get("merged")), len(attempted))
    objs.append(Objective("A3", "Merge-conflict rate", a3, "< 10%", "%", None if a3 is None else a3 < 10))

    # A4 plan fidelity: subtasks that succeeded (agent reported done) of all planned.
    a4 = _pct(sum(1 for s in subs if s.get("success")), len(subs))
    objs.append(Objective("A4", "Plan fidelity", a4, ">= 95%", "%", None if a4 is None else a4 >= 95))

    # E1 tokens per completed subtask.
    completed = [s for s in subs if s.get("accepted")]
    e1 = (sum(s.get("tokens_total", 0) for s in completed) / len(completed)) if completed else None
    objs.append(Objective("E1", "Tokens / subtask", e1, "tracked", "tok", None))

    # E2 cache-hit ratio (mean across subtasks).
    ratios = [s.get("cache_hit_ratio", 0.0) for s in subs if s.get("cache_hit_ratio") is not None]
    e2 = (100.0 * sum(ratios) / len(ratios)) if ratios else None
    objs.append(Objective("E2", "Cache-hit ratio", e2, ">= 60%", "%", None if e2 is None else e2 >= 60))

    return objs


def _median(xs: list[float]) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def gate(objs: list[Objective]) -> bool:
    """Return True if no hard objective with data is failing."""
    return all(o.passed is not False for o in objs)
