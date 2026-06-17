"""Orchestrator: DAG scheduling + branched fan-out of auto-approved agents.

For each subtask: create an isolated worktree, run an auto-approved headless Claude
session, run the subtask's acceptance checks, retry once on failure, then merge green
branches back. Streams live progress and records every speed/accuracy/efficiency signal.
"""

from __future__ import annotations

import asyncio
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import metrics, vscode
from .agent_session import AgentResult, run_agent
from .approval import ApprovalPolicy
from .config import Config
from .context_budget import clip
from .dashboard import Dashboard
from .kimi_session import run_kimi_agent
from .memory import SessionMemory
from .planner import Plan, Subtask
from .prompts import AGENT_SYSTEM_PREFIX, agent_prompt
from .repo_context import RepoContext, gather
from .skills import Skill, SkillStore
from .worktree import WorktreeManager


@dataclass
class SubtaskOutcome:
    subtask: Subtask
    result: AgentResult
    accepted: bool
    first_attempt: bool
    attempts: int
    check_output: str = ""
    branch: str | None = None
    merged: bool = False
    merge_attempted: bool = False


@dataclass
class RunReport:
    run_id: str
    task: str
    mode: str
    outcomes: list[SubtaskOutcome] = field(default_factory=list)
    wall_ms: int = 0
    objective_records: list = field(default_factory=list)
    progress_path: str = ""

    @property
    def n_accepted(self) -> int:
        return sum(1 for o in self.outcomes if o.accepted)

    @property
    def n_merged(self) -> int:
        return sum(1 for o in self.outcomes if o.merged)


def topo_waves(subtasks: list[Subtask]) -> list[list[Subtask]]:
    """Group subtasks into dependency waves (each wave runs in parallel)."""
    by_id = {s.id: s for s in subtasks}
    done: set[str] = set()
    waves: list[list[Subtask]] = []
    remaining = list(subtasks)
    while remaining:
        wave = [s for s in remaining if all(d in done for d in s.deps if d in by_id)]
        if not wave:  # dependency cycle — break it by running the rest as one wave
            wave = remaining
        waves.append(wave)
        for s in wave:
            done.add(s.id)
        remaining = [s for s in remaining if s.id not in done]
    return waves


def run_checks(checks: list[str], cwd: Path, timeout: int = 600) -> tuple[bool, str]:
    """Run acceptance-check shell commands; return (all_passed, combined_output)."""
    if not checks:
        return True, "(no acceptance checks)"
    outputs = []
    ok = True
    for cmd in checks:
        try:
            proc = subprocess.run(
                cmd, cwd=str(cwd), shell=True, capture_output=True, text=True, timeout=timeout
            )
            passed = proc.returncode == 0
            ok = ok and passed
            tail = clip((proc.stdout + proc.stderr).strip(), 800)
            outputs.append(f"$ {cmd}\n[exit {proc.returncode}] {'OK' if passed else 'FAIL'}\n{tail}")
        except (subprocess.SubprocessError, OSError) as e:
            ok = False
            outputs.append(f"$ {cmd}\n[error] {e}")
    return ok, "\n\n".join(outputs)


class Orchestrator:
    def __init__(self, cfg: Config, policy: ApprovalPolicy):
        self.cfg = cfg
        self.policy = policy

    async def _backend(self, name: str, prompt: str, cwd, on_event) -> AgentResult:
        """Dispatch to the configured agent backend (Claude Code headless or Kimi /v1)."""
        if self.cfg.provider == "kimi":
            return await run_kimi_agent(
                self.cfg, self.policy, name, prompt, cwd,
                on_event=on_event, append_system=AGENT_SYSTEM_PREFIX,
            )
        return await run_agent(
            self.cfg, self.policy, name, prompt, cwd,
            on_event=on_event, append_system=AGENT_SYSTEM_PREFIX,
        )

    async def _run_subtask(
        self,
        subtask: Subtask,
        manager: WorktreeManager,
        skill_hint: str,
        memory: SessionMemory,
        dashboard: Dashboard,
        sem: asyncio.Semaphore,
    ) -> SubtaskOutcome:
        async with sem:
            dashboard.set_status(subtask.id, "running")
            wt = manager.create(subtask.id)
            # Gather context from the agent's OWN worktree so paths it sees match its cwd
            # (agents otherwise write to the path shown in the summary, not their sandbox).
            wctx = gather(wt.path)
            workspace_summary = wctx.render()
            block = subtask.as_block()
            if skill_hint:
                block += f"\n\nPrior approaches that worked here:\n{skill_hint}"
            prompt = agent_prompt(block, workspace_summary, cwd=str(wt.path))

            result = await self._backend(subtask.id, prompt, wt.path, dashboard.on_event)
            memory.append_transcript("agent", f"[{subtask.id}] {clip(result.final_text, 300)}")

            dashboard.set_status(subtask.id, "checking", "acceptance checks")
            accepted, check_out = await asyncio.to_thread(run_checks, subtask.acceptance_checks, wt.path)
            memory.append_transcript(
                "checks", f"[{subtask.id}] attempt 1 {'PASS' if accepted else 'FAIL'}", clip(check_out, 1200)
            )
            attempts = 1
            first_attempt = accepted

            # Retry once with failure context (Raschka: bounded self-correction).
            while not accepted and attempts <= self.cfg.retry_failed:
                dashboard.set_status(subtask.id, "running", f"retry {attempts}")
                retry_prompt = (
                    f"{agent_prompt(block, workspace_summary, cwd=str(wt.path))}\n\n"
                    f"Your previous attempt did not pass the acceptance checks. Output:\n"
                    f"{clip(check_out, 1500)}\n\nFix the failures and finish."
                )
                result = await self._backend(subtask.id, retry_prompt, wt.path, dashboard.on_event)
                accepted, check_out = await asyncio.to_thread(
                    run_checks, subtask.acceptance_checks, wt.path
                )
                memory.append_transcript(
                    "checks",
                    f"[{subtask.id}] retry {attempts} {'PASS' if accepted else 'FAIL'}",
                    clip(check_out, 1200),
                )
                attempts += 1

            dashboard.set_status(subtask.id, "done" if accepted else "failed")
            return SubtaskOutcome(
                subtask=subtask,
                result=result,
                accepted=accepted,
                first_attempt=first_attempt,
                attempts=attempts,
                check_output=check_out,
                branch=wt.branch,
            )

    async def run(self, plan: Plan, ctx: RepoContext, mode: str = "orchestrated") -> RunReport:
        cfg = self.cfg
        cfg.ensure_dirs()
        run_id = time.strftime("%Y%m%d-%H%M%S") + f"-{mode}"
        run_dir = cfg.runs_dir / run_id
        memory = SessionMemory(run_dir, plan.task)
        memory.working.task = plan.task
        memory.working.open_subtasks = [s.id for s in plan.subtasks]
        skills = SkillStore(cfg.skills_dir)

        recalled = skills.recall(plan.task)
        skill_hint = "\n".join(f"- {s.name}: {s.approach}" for s in recalled)

        concurrency = 1 if mode == "serial" else cfg.concurrency
        sem = asyncio.Semaphore(concurrency)
        manager = WorktreeManager(cfg.repo_root, cfg.worktrees_dir)

        report = RunReport(run_id=run_id, task=plan.task, mode=mode)
        # Live progress document — surfaced in VSCode as an auto-reloading editor tab.
        progress_path = run_dir / "progress.md"
        report.progress_path = str(progress_path)
        vscode_on = (cfg.vscode if cfg.vscode is not None else vscode.available()) and mode != "serial"
        dashboard = Dashboard(
            f"sidekick · {mode} · {clip(plan.task, 50)}",
            progress_path=progress_path,
            concurrency=concurrency,
        )
        for s in plan.subtasks:
            dashboard.register(s.id)
        if vscode_on:
            dashboard.finalize()  # write the initial doc before opening it
            vscode.open_file(progress_path)

        wall_start = time.monotonic()
        with dashboard:
            for wave in topo_waves(plan.subtasks):
                # Re-resolve base so dependent waves branch from merged dependency work.
                manager.refresh_base()
                coros = [
                    self._run_subtask(s, manager, skill_hint, memory, dashboard, sem)
                    for s in wave
                ]
                outcomes = await asyncio.gather(*coros)
                # Merge green branches sequentially into the base branch (A3 metric).
                for o in outcomes:
                    o.merge_attempted = o.accepted
                    if o.accepted:
                        o.merged = _commit_and_merge(manager, o)
                        memory.working.done_subtasks.append(o.subtask.id)
                    report.outcomes.append(o)

        report.wall_ms = int((time.monotonic() - wall_start) * 1000)
        memory.save_working()

        # Final progress footer + surface the changed files in VSCode for review.
        footer = (
            f"## Result\n\n**{report.n_accepted}/{len(report.outcomes)} accepted · "
            f"{report.n_merged} merged · wall {report.wall_ms / 1000:.1f}s**\n\n"
            + "\n".join(
                f"- {'✅' if o.accepted else '❌'} `{o.subtask.id}` — "
                f"attempts={o.attempts}, merged={o.merged}"
                for o in report.outcomes
            )
        )
        dashboard.finalize(footer)
        if vscode_on:
            changed = []
            for o in report.outcomes:
                if not o.accepted:
                    continue
                for f in o.subtask.target_files:
                    p = (cfg.repo_root / f).resolve()
                    if p.exists() and p not in changed:
                        changed.append(p)
            for p in changed[:12]:
                vscode.open_file(p)

        # Record metrics.
        agent_ms_sum = sum(o.result.wall_ms for o in report.outcomes)
        run_rec = metrics.RunRecord(
            run_id=run_id,
            task=plan.task,
            mode=mode,
            concurrency=concurrency,
            n_subtasks=len(plan.subtasks),
            wall_ms=report.wall_ms,
            agent_ms_sum=agent_ms_sum,
            human_wait_ms=0,  # full auto-approval → no human wait (S4)
        )
        metrics.append(cfg.metrics_path, run_rec)
        report.objective_records.append(run_rec)
        for o in report.outcomes:
            r = o.result
            sub_rec = metrics.SubtaskRecord(
                run_id=run_id,
                subtask_id=o.subtask.id,
                success=r.success,
                accepted=o.accepted,
                first_attempt=o.first_attempt and o.accepted,
                merged=o.merged,
                merge_attempted=o.merge_attempted,
                wall_ms=r.wall_ms,
                ttft_ms=r.ttft_ms,
                time_to_first_edit_ms=r.time_to_first_edit_ms,
                num_turns=r.num_turns,
                tokens_total=sum(r.tokens.values()),
                cost_usd=r.cost_usd,
                cache_hit_ratio=r.cache_hit_ratio,
            )
            metrics.append(cfg.metrics_path, sub_rec)
            report.objective_records.append(sub_rec)

        # Distill a skill from a fully-successful run (Hermes learning loop).
        if mode == "orchestrated" and report.n_accepted == len(plan.subtasks) and plan.subtasks:
            checks = sorted({c for s in plan.subtasks for c in s.acceptance_checks})
            skills.save(
                Skill(
                    name=clip(plan.task, 48),
                    trigger=plan.task,
                    approach=f"Decomposed into {len(plan.subtasks)} parallel subtasks; all passed.",
                    acceptance_checks=checks,
                )
            )

        return report


def _commit_and_merge(manager: WorktreeManager, outcome: SubtaskOutcome) -> bool:
    from .worktree import Worktree

    wt = Worktree(
        path=manager.worktrees_dir / outcome.subtask.id,
        branch=outcome.branch or f"sidekick/{outcome.subtask.id}",
        base=manager.base,
        root=manager.root,
    )
    made = manager.commit_all(wt, f"sidekick[{outcome.subtask.id}]: {outcome.subtask.title}")
    if not made:
        return False
    return manager.merge_clean(wt)
