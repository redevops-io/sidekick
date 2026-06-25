"""Structured session memory (Raschka #5).

Two layers persisted as JSON on disk:
  * transcript — durable, append-only record of every event (resumable).
  * working_memory — small, explicitly-maintained summary of current task, key files,
    and recent notes used to keep agents on-task.

Ported from Hermes 0.17 ("atomic batch operations"): `save_working()` now persists
crash-safely via a temp-file + atomic rename, and `batch()` groups a set of mutations so
they commit all-or-nothing — a single transcript append + one working-memory persist, and
nothing at all if the block raises.
"""

from __future__ import annotations

import copy
import json
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` via a temp file + atomic rename (no torn/partial files)."""
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


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
        # While a batch() is active this holds buffered transcript records; None otherwise.
        self._batch: list[dict] | None = None

    def _write_transcript(self, recs: list[dict]) -> None:
        if not recs:
            return
        with self.transcript_path.open("a", encoding="utf-8") as f:
            for rec in recs:
                f.write(json.dumps(rec) + "\n")

    def append_transcript(self, role: str, summary: str, detail: str = "") -> None:
        rec = {"ts": time.time(), "role": role, "summary": summary, "detail": detail}
        if self._batch is not None:  # defer to the active batch — flushed on clean exit
            self._batch.append(rec)
            return
        self._write_transcript([rec])

    def append_transcript_batch(self, records: list[dict]) -> None:
        """Append several transcript records in one file write.

        Each record is a mapping with `role`/`summary` (and optional `detail`). Inside a
        batch() the records join the buffer instead of being written immediately.
        """
        recs = [
            {
                "ts": time.time(),
                "role": str(r.get("role", "")),
                "summary": str(r.get("summary", "")),
                "detail": str(r.get("detail", "")),
            }
            for r in records
        ]
        if self._batch is not None:
            self._batch.extend(recs)
            return
        self._write_transcript(recs)

    def save_working(self) -> None:
        _atomic_write_text(self.working_path, json.dumps(asdict(self.working), indent=2))

    @contextmanager
    def batch(self) -> Iterator[SessionMemory]:
        """Apply a group of memory mutations atomically (Hermes 0.17 batch ops).

        Transcript appends made inside the block are buffered and flushed in a single
        write, and working memory is persisted exactly once — both only if the block exits
        cleanly. If it raises, buffered transcript records are dropped and in-memory
        working changes are rolled back, leaving on-disk state untouched.
        """
        if self._batch is not None:
            raise RuntimeError("memory batch already active (batches do not nest)")
        snapshot = copy.deepcopy(self.working)
        self._batch = []
        try:
            yield self
        except BaseException:
            self.working = snapshot
            self._batch = None
            raise
        buffered, self._batch = self._batch, None
        self._write_transcript(buffered)
        self.save_working()

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
