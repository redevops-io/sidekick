"""sidekick command-line interface.

  sidekick run "<task>"     decompose, fan out auto-approved agents, merge, report
  sidekick plan "<task>"    print the subtask plan only
  sidekick metrics          print the objective table from metrics.jsonl
  sidekick status           show the last run's working memory
  sidekick bench            run the seed benchmark (serial baseline vs orchestrated)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from . import metrics as M
from . import voice as V
from .approval import ApprovalPolicy
from .config import Config
from .orchestrator import Orchestrator
from .planner import load_plan, make_plan, save_plan
from .repo_context import gather

try:
    from rich.console import Console
    from rich.table import Table

    _console = Console()
except Exception:  # pragma: no cover
    _console = None


def _print(msg: str = "") -> None:
    if _console:
        _console.print(msg)
    else:  # pragma: no cover
        print(msg)


def render_objectives(objs: list[M.Objective]) -> None:
    if _console:
        table = Table(title="sidekick objectives", expand=False)
        for col in ("id", "objective", "value", "target", "status"):
            table.add_column(col)
        for o in objs:
            if o.value is None:
                val, status, style = "—", "no data", "dim"
            else:
                val = f"{o.value:,.1f}{o.unit}" if o.unit != "x" else f"{o.value:.2f}x"
                if o.passed is None:
                    status, style = "info", "cyan"
                elif o.passed:
                    status, style = "PASS", "green"
                else:
                    status, style = "MISS", "red"
            table.add_row(o.id, o.name, val, o.target, f"[{style}]{status}[/{style}]")
        _console.print(table)
    else:  # pragma: no cover
        for o in objs:
            print(f"{o.id} {o.name}: {o.value} (target {o.target})")


def _mk_config(args) -> Config:
    cfg = Config(repo_root=Path(args.repo).resolve())
    if getattr(args, "concurrency", None):
        cfg.concurrency = args.concurrency
    if getattr(args, "approval", None):
        cfg.approval = args.approval
    if getattr(args, "model", None):
        cfg.agent_model = args.model
    if getattr(args, "vscode", None) is not None:
        cfg.vscode = args.vscode
    if getattr(args, "provider", None):
        cfg.provider = args.provider
    if getattr(args, "kimi_model", None):
        cfg.kimi_model = args.kimi_model
    if getattr(args, "kimi_base_url", None):
        cfg.kimi_base_url = args.kimi_base_url
    if getattr(args, "kimi_key", None):
        cfg.kimi_api_key = args.kimi_key
    return cfg


def cmd_plan(args) -> int:
    cfg = _mk_config(args)
    ctx = gather(cfg.repo_root)
    plan = make_plan(cfg, ctx, args.task, max_subtasks=args.max_subtasks)
    _print(json.dumps(plan.to_dict(), indent=2))
    return 0


def _confirm_plan(plan) -> bool:
    _print(f"\n[bold]Plan for:[/bold] {plan.task}" if _console else f"\nPlan for: {plan.task}")
    for s in plan.subtasks:
        deps = f" (after {', '.join(s.deps)})" if s.deps else ""
        _print(f"  • [{s.id}]{deps} {s.title}")
        if s.acceptance_checks:
            _print(f"      checks: {'; '.join(s.acceptance_checks)}")
    if not sys.stdin.isatty():
        return True
    try:
        resp = input("\nProceed? [Y/n] ").strip().lower()
    except EOFError:
        return True
    return resp in ("", "y", "yes")


def _orchestrate(cfg: Config, ctx, plan) -> int:
    """Run a plan to completion, print the result + objective table."""
    policy = ApprovalPolicy(level=cfg.approval)
    backend = f"kimi:{cfg.kimi_model}" if cfg.provider == "kimi" else f"claude:{cfg.agent_model or 'default'}"
    _print(
        f"[dim]Running {len(plan.subtasks)} subtask(s) · backend={backend} · "
        f"concurrency={cfg.concurrency} · approval={policy.describe()}[/dim]"
        if _console
        else f"Running {len(plan.subtasks)} subtask(s) · backend={backend} · concurrency={cfg.concurrency}"
    )
    report = asyncio.run(Orchestrator(cfg, policy).run(plan, ctx, mode="orchestrated"))
    _print(
        f"\n[bold]Done:[/bold] {report.n_accepted}/{len(report.outcomes)} accepted, "
        f"{report.n_merged} merged, wall {report.wall_ms/1000:.1f}s"
        if _console
        else f"\nDone: {report.n_accepted}/{len(report.outcomes)} accepted, "
        f"{report.n_merged} merged, wall {report.wall_ms/1000:.1f}s"
    )
    for o in report.outcomes:
        flag = "✓" if o.accepted else "✗"
        _print(f"  {flag} [{o.subtask.id}] attempts={o.attempts} merged={o.merged}")
    if report.progress_path:
        _print(
            f"[dim]live progress doc: {report.progress_path}[/dim]"
            if _console
            else f"progress: {report.progress_path}"
        )
    render_objectives(M.compute(M.load(cfg.metrics_path)))
    return 0 if report.n_accepted == len(report.outcomes) else 2


def cmd_run(args) -> int:
    cfg = _mk_config(args)
    ctx = gather(cfg.repo_root)
    if args.plan_file:
        plan = load_plan(Path(args.plan_file))
    else:
        _print("[dim]planning…[/dim]" if _console else "planning…")
        plan = make_plan(cfg, ctx, args.task, max_subtasks=args.max_subtasks)
    cfg.ensure_dirs()
    save_plan(plan, cfg.state_dir / "last_plan.json")

    if not args.yes and not _confirm_plan(plan):
        _print("Aborted.")
        return 1
    return _orchestrate(cfg, ctx, plan)


def cmd_repl(args) -> int:
    """Interactive loop: type a coding task, sidekick fans it out. Auto-launchable on
    VSCode folder-open so sidekick is your default coding workflow."""
    cfg = _mk_config(args)
    cfg.ensure_dirs()
    banner = (
        f"sidekick repl · repo={cfg.repo_root.name} · concurrency={cfg.concurrency} · "
        f"approval={ApprovalPolicy(cfg.approval).describe()}\n"
        "Type a coding task and press Enter (sidekick plans → fans out → merges). "
        "Ctrl-D or 'exit' to quit."
    )
    _print(f"[bold cyan]{banner}[/bold cyan]" if _console else banner)
    while True:
        if getattr(args, "voice", False):
            task = _capture_task_voice(args.seconds)
            if task is None:
                _print("(no input — Ctrl-C again to exit)")
                continue
            task = task.strip()
        else:
            try:
                task = input("\nsidekick> ").strip()
            except (EOFError, KeyboardInterrupt):
                _print("\nbye.")
                return 0
        if not task:
            continue
        if task in ("exit", "quit"):
            return 0
        ctx = gather(cfg.repo_root)
        _print("[dim]planning…[/dim]" if _console else "planning…")
        plan = make_plan(cfg, ctx, task, max_subtasks=args.max_subtasks)
        save_plan(plan, cfg.state_dir / "last_plan.json")
        if not args.yes and not _confirm_plan(plan):
            _print("skipped.")
            continue
        _orchestrate(cfg, ctx, plan)


def _capture_task_voice(seconds: int | None) -> str | None:
    """Record a spoken task and return the transcript, or None on error/empty."""
    if not V.available():
        _print("voice unavailable: need ffmpeg/arecord + an STT key (e.g. OPENAI_API_KEY).")
        return None
    try:
        input("[voice] press Enter, then speak your task…")
    except (EOFError, KeyboardInterrupt):
        return None
    _print("[dim]listening…[/dim]" if _console else "listening…")
    try:
        task = V.listen(seconds)
    except V.VoiceError as e:
        _print(f"voice error: {e}")
        return None
    _print(f"[bold]heard:[/bold] {task}" if _console else f"heard: {task}")
    return task or None


def cmd_voice(args) -> int:
    cfg = _mk_config(args)
    cfg.ensure_dirs()
    task = _capture_task_voice(args.seconds)
    if not task:
        return 1
    if args.transcribe_only:
        return 0
    ctx = gather(cfg.repo_root)
    _print("[dim]planning…[/dim]" if _console else "planning…")
    plan = make_plan(cfg, ctx, task, max_subtasks=args.max_subtasks)
    save_plan(plan, cfg.state_dir / "last_plan.json")
    if not args.yes and not _confirm_plan(plan):
        _print("Aborted.")
        return 1
    return _orchestrate(cfg, ctx, plan)


def cmd_metrics(args) -> int:
    cfg = _mk_config(args)
    render_objectives(M.compute(M.load(cfg.metrics_path)))
    return 0


def cmd_status(args) -> int:
    cfg = _mk_config(args)
    runs = sorted(cfg.runs_dir.glob("*/working_memory.json")) if cfg.runs_dir.exists() else []
    if not runs:
        _print("No runs yet.")
        return 0
    data = json.loads(runs[-1].read_text())
    _print(f"[bold]Last run:[/bold] {runs[-1].parent.name}" if _console else f"Last run: {runs[-1].parent.name}")
    _print(f"  task: {data.get('task','')}")
    _print(f"  done: {', '.join(data.get('done_subtasks', [])) or '—'}")
    _print(f"  open: {', '.join(data.get('open_subtasks', [])) or '—'}")
    return 0


def cmd_bench(args) -> int:
    from experiments.bench import run_bench

    return run_bench(repo=args.repo, concurrency=args.concurrency or 3, keep=args.keep)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="sidekick", description="Local coding-agent orchestrator.")
    p.add_argument("--repo", default=".", help="Target repository root (default: cwd)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_agent_opts(sp):
        sp.add_argument("--concurrency", type=int, help="Max parallel agents")
        sp.add_argument("--approval", help="accept_edits_allowlist | bypass | edits_no_bash")
        sp.add_argument("--model", help="Model for spawned agents (default: inherit)")
        sp.add_argument("--max-subtasks", type=int, default=6, dest="max_subtasks")
        sp.add_argument("--provider", help="Agent backend: claude | kimi (default: kimi on this branch)")
        sp.add_argument("--kimi-model", dest="kimi_model", help="Override KIMI_AGENT_MODEL")
        sp.add_argument("--kimi-base-url", dest="kimi_base_url", help="Override KIMI_AGENT_BASE_URL")
        sp.add_argument("--kimi-key", dest="kimi_key", help="Override KIMI_AGENT_API_KEY (manual)")
        sp.add_argument(
            "--vscode", dest="vscode", action="store_true", default=None,
            help="Open the live progress doc + changed files in VSCode (default: auto-detect)",
        )
        sp.add_argument(
            "--no-vscode", dest="vscode", action="store_false",
            help="Disable VSCode integration",
        )

    rp = sub.add_parser("run", help="Decompose and run a task")
    rp.add_argument("task", nargs="?", default="", help="High-level task")
    rp.add_argument("--plan-file", help="Run a saved plan JSON instead of planning")
    rp.add_argument("-y", "--yes", action="store_true", help="Skip plan confirmation")
    add_agent_opts(rp)
    rp.set_defaults(func=cmd_run)

    rep = sub.add_parser("repl", help="Interactive task loop (auto-launch on VSCode open)")
    rep.add_argument("-y", "--yes", action="store_true", help="Skip plan confirmation per task")
    rep.add_argument("--voice", action="store_true", help="Speak each task instead of typing")
    rep.add_argument("--seconds", type=int, default=None, help="Voice clip length (default 8)")
    add_agent_opts(rep)
    rep.set_defaults(func=cmd_repl)

    vp = sub.add_parser("voice", help="Speak one coding task and run it")
    vp.add_argument("--seconds", type=int, default=None, help="Recording length (default 8)")
    vp.add_argument("-y", "--yes", action="store_true", help="Skip plan confirmation")
    vp.add_argument(
        "--transcribe-only", dest="transcribe_only", action="store_true",
        help="Just print the transcript; don't run it",
    )
    add_agent_opts(vp)
    vp.set_defaults(func=cmd_voice)

    pp = sub.add_parser("plan", help="Print the subtask plan only")
    pp.add_argument("task", help="High-level task")
    add_agent_opts(pp)
    pp.set_defaults(func=cmd_plan)

    mp = sub.add_parser("metrics", help="Print the objective table")
    mp.set_defaults(func=cmd_metrics)

    stp = sub.add_parser("status", help="Show the last run's state")
    stp.set_defaults(func=cmd_status)

    bp = sub.add_parser("bench", help="Run the seed benchmark")
    bp.add_argument("--concurrency", type=int, default=3)
    bp.add_argument("--keep", action="store_true", help="Keep the scratch bench repo")
    bp.set_defaults(func=cmd_bench)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cmd == "run" and not args.task and not args.plan_file:
        _print("error: provide a task or --plan-file")
        return 2
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
