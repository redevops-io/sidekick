"""Seed benchmark: serial baseline vs orchestrated fan-out.

Runs a fixed, disjoint 3-subtask plan on fresh scratch repos in both modes so the
objective table (especially S2 parallel speedup) is measured, not guessed. Uses a small
fast model by default to keep the benchmark cheap and quick.

Usage: loopie bench  [--concurrency 3] [--keep]
"""

from __future__ import annotations

import asyncio
import dataclasses
import os
import shutil
import subprocess
from pathlib import Path

from loopie import metrics as M
from loopie.approval import ApprovalPolicy
from loopie.config import Config
from loopie.orchestrator import Orchestrator
from loopie.planner import Plan, Subtask
from loopie.repo_context import gather


def build_seed_plan() -> Plan:
    specs = [
        ("alpha", "add", "a + b", "alpha.add(2, 3) == 5"),
        ("beta", "mul", "a * b", "beta.mul(2, 3) == 6"),
        ("gamma", "sub", "a - b", "gamma.sub(5, 3) == 2"),
    ]
    subs = []
    for sid, fn, body, assertion in specs:
        subs.append(
            Subtask(
                id=sid,
                title=f"create {sid}.py with {fn}()",
                description=(
                    f"Create a file named `{sid}.py` in your current working directory containing "
                    f"exactly one function: `def {fn}(a, b):` that returns `{body}`. No other code."
                ),
                target_files=[f"{sid}.py"],
                deps=[],
                acceptance_checks=[f'python3 -c "import {sid}; assert {assertion}"'],
            )
        )
    return Plan(task="bench: build three independent arithmetic modules", subtasks=subs)


def _fresh_repo(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "bench@loopie"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "loopie-bench"], cwd=path, check=True)
    subprocess.run(["git", "commit", "--allow-empty", "-q", "-m", "base"], cwd=path, check=True)


def _mk_cfg(repo: Path, concurrency: int) -> Config:
    cfg = Config(repo_root=repo)
    cfg.concurrency = concurrency
    # Cheap, fast model for the benchmark unless the user overrides via env.
    if not os.environ.get("LOOPIE_AGENT_MODEL"):
        cfg.agent_model = "claude-haiku-4-5-20251001"
    return cfg


def run_bench(repo: str = ".", concurrency: int = 3, keep: bool = False) -> int:
    base = Path("/tmp/loopie_bench")
    serial_repo = base / "serial"
    orch_repo = base / "orch"
    plan = build_seed_plan()
    policy = ApprovalPolicy()

    all_records: list[dict] = []
    for mode, rdir, conc in (("serial", serial_repo, 1), ("orchestrated", orch_repo, concurrency)):
        _fresh_repo(rdir)
        cfg = _mk_cfg(rdir, conc)
        ctx = gather(cfg.repo_root)
        orch = Orchestrator(cfg, policy)
        print(f"[bench] running {mode} (concurrency={conc}) …", flush=True)
        report = asyncio.run(orch.run(plan, ctx, mode=mode))
        print(
            f"[bench] {mode}: {report.n_accepted}/{len(report.outcomes)} accepted, "
            f"wall {report.wall_ms/1000:.1f}s",
            flush=True,
        )
        all_records.extend(dataclasses.asdict(r) for r in report.objective_records)

    # Persist combined records and print the objective table.
    bench_metrics = base / "metrics.jsonl"
    bench_metrics.parent.mkdir(parents=True, exist_ok=True)
    with bench_metrics.open("w", encoding="utf-8") as f:
        import json

        for r in all_records:
            f.write(json.dumps(r) + "\n")

    objs = M.compute(all_records)
    _render(objs)
    passed = M.gate(objs)
    print(f"\n[bench] objective gate: {'PASS' if passed else 'FAIL'} · records → {bench_metrics}")
    if not keep:
        shutil.rmtree(base, ignore_errors=True)
    return 0 if passed else 1


def _render(objs: list[M.Objective]) -> None:
    try:
        from loopie.cli import render_objectives

        render_objectives(objs)
    except Exception:
        for o in objs:
            print(f"{o.id} {o.name}: {o.value} (target {o.target})")


if __name__ == "__main__":
    raise SystemExit(run_bench())
