"""Structured session memory (Raschka #5).

Two layers persisted as JSON on disk:
  * transcript — durable, append-only record of every event (resumable).
  * working_memory — small, explicitly-maintained summary of current task, key files,
    and recent notes used to keep agents on-task.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class WorkingMemory:
    task: str = ""
    key_files: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    open_subtasks: list[str] = field(default_factory=list)
    done_subtasks: list[str] = field(default_factory=list)

    def note(self, msg: str, cap: int = 50) -> None:
        self.notes.append(msg)
        if len(self.notes) > cap:
            self.notes = self.notes[-cap:]


class SessionMemory:
    def __init__(self, run_dir: Path, task: str = ""):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.transcript_path = self.run_dir / "transcript.jsonl"
        self.working_path = self.run_dir / "working_memory.json"
        self.working = WorkingMemory(task=task)
        if self.working_path.exists():
            try:
                self.working = WorkingMemory(**json.loads(self.working_path.read_text()))
            except (json.JSONDecodeError, TypeError):
                pass

    def append_transcript(self, role: str, summary: str, detail: str = "") -> None:
        rec = {"ts": time.time(), "role": role, "summary": summary, "detail": detail}
        with self.transcript_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec) + "\n")

    def save_working(self) -> None:
        self.working_path.write_text(json.dumps(asdict(self.working), indent=2), encoding="utf-8")

    def load_transcript(self) -> list[dict]:
        if not self.transcript_path.exists():
            return []
        out = []
        for line in self.transcript_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
        return out
