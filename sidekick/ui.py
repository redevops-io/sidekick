"""Sidekick UI capabilities — goal-based testing, quality audit and Time-to-Capability benchmarking.

Thin wrapper over the reusable ReDevOps `ui-agent` / `redevops_ui` engine (its home is the agentic-tests
repo; installed here via the `ui` extra). We invoke the engine as a separate process — matching sidekick's
own separate-process philosophy — and read back its structured JSON, so there is exactly one implementation
of each capability and no Python-version coupling.

The product rule (SIDEKICK_TEST_PRODUCT report §6): the execution agent is NOT allowed to grade its own
work. `run_test` requires an explicit deterministic success predicate; the engine verifies it on the real
page and never trusts the agent's DONE claim.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Dict, List, Optional

_ENGINE_HINT = ("UI engine not found. Install it with the `ui` extra "
                "(`uv sync --extra ui && uv run playwright install chromium`), "
                "which pulls the redevops ui-agent engine + Playwright.")


class EngineUnavailable(RuntimeError):
    """The ui-agent engine (redevops_ui) is not installed/importable."""


def _engine_argv() -> List[str]:
    """Prefer the `ui-agent` console script; fall back to running the module in this interpreter."""
    exe = shutil.which("ui-agent")
    if exe:
        return [exe]
    return [sys.executable, "-m", "ui_agent.cli"]


def _invoke(argv: List[str], *, json_out: bool = True) -> Dict[str, Any]:
    """Run the engine CLI, capturing its exit code, stdout, and (optionally) its --json artifact."""
    tmp: Optional[str] = None
    full = _engine_argv() + argv
    if json_out:
        fd, tmp = tempfile.mkstemp(suffix=".json")
        os.close(fd)
        full += ["--json", tmp]
    try:
        proc = subprocess.run(full, text=True, capture_output=True)
    except FileNotFoundError as e:  # neither script nor module runnable
        raise EngineUnavailable(_ENGINE_HINT) from e
    if proc.returncode == 2 and "No module named" in (proc.stderr or ""):
        raise EngineUnavailable(_ENGINE_HINT)
    data: Any = None
    if tmp and os.path.exists(tmp):
        try:
            raw = open(tmp, encoding="utf-8").read()
            data = json.loads(raw) if raw.strip() else None
        except Exception:  # noqa: BLE001 — a missing/partial artifact is not fatal to reporting
            data = None
        finally:
            os.unlink(tmp)
    return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "result": data}


# --------------------------------------------------------------------------- capabilities
def run_test(url: str, goal: str, expect: str, *, steps: Optional[List[str]] = None,
             max_steps: int = 25, headed: bool = False) -> Dict[str, Any]:
    """Drive a natural-language journey and PROVE the outcome with a deterministic predicate `expect`
    (usable_state:N | selector:CSS | selector_text:CSS=TEXT | url_contains:V)."""
    argv = ["journey", "--capability", goal, "--url", url, "--success", expect, "--max-steps", str(max_steps)]
    for s in (steps or [goal]):
        argv += ["--step", s]
    if headed:
        argv += ["--headed"]
    return _invoke(argv, json_out=True)


def run_audit(*, url: Optional[str] = None, service_type: Optional[str] = None,
              product: Optional[str] = None, all_targets: bool = False,
              assert_regressions: Optional[str] = None) -> Dict[str, Any]:
    """Deterministic WCAG / Core Web Vitals / browser-health audit of a page, a product, or the portfolio."""
    argv = ["audit-ui"]
    if url:
        argv += ["--url", url]
        if service_type:
            argv += ["--type", service_type]
    elif product:
        argv += ["--product", product]
    else:
        argv += ["--all"]
    if assert_regressions:
        argv += ["--assert-regressions", assert_regressions]
    return _invoke(argv, json_out=True)


def run_benchmark(capability: str, *, targets: Optional[List[str]] = None) -> Dict[str, Any]:
    """Time-to-Capability head-to-head: the same capability across targets, driven identically."""
    argv = ["benchmark", "--capability", capability]
    for t in (targets or []):
        argv += ["--target", t]
    return _invoke(argv, json_out=True)
