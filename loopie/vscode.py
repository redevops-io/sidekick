"""VSCode integration.

loopie orchestrates *headless* Claude Code sessions (so they can be auto-approved and run
in parallel), which means they do not show up as interactive sessions in the VSCode
sidebar. Instead, progress is surfaced inside the editor by:

  * writing a live `progress.md` that the dashboard updates continuously — opened in an
    editor tab, VSCode auto-reloads it on change, so you watch the fan-out in the editor;
  * opening the changed files (and optional diffs) for review when the run completes.

All actions are best-effort and no-op when the `code` CLI or a VSCode session is absent,
so loopie still works headlessly / in CI.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


def code_bin() -> str | None:
    return shutil.which("code")


def in_vscode() -> bool:
    return bool(os.environ.get("VSCODE_PID") or os.environ.get("TERM_PROGRAM") == "vscode")


def available() -> bool:
    """True when we can drive VSCode (the `code` CLI exists)."""
    return code_bin() is not None


def open_file_cmd(path: Path | str, reuse: bool = True) -> list[str]:
    args = ["code"]
    if reuse:
        args.append("-r")  # reuse the current window instead of opening a new one
    args.append(str(path))
    return args


def open_diff_cmd(left: Path | str, right: Path | str) -> list[str]:
    return ["code", "--diff", str(left), str(right)]


def _run(cmd: list[str]) -> bool:
    binary = code_bin()
    if not binary:
        return False
    try:
        subprocess.run([binary, *cmd[1:]], check=False, capture_output=True, timeout=15)
        return True
    except (OSError, subprocess.SubprocessError):
        return False


def open_file(path: Path | str, reuse: bool = True) -> bool:
    return _run(open_file_cmd(path, reuse))


def open_diff(left: Path | str, right: Path | str) -> bool:
    return _run(open_diff_cmd(left, right))
