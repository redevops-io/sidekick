"""Procedural memory / skills (Hermes-style learning loop).

After a successful run, loopie distills a lightweight, reusable "skill" — a named recipe
(task pattern -> approach + acceptance checks that worked) stored as JSON. On a new task,
recall() does a simple keyword match to surface relevant prior skills, which the planner
can use as hints. This is the minimal version of Hermes' "creates skills from experience"
loop, with FTS approximated by token-overlap scoring.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

_WORD = re.compile(r"[a-zA-Z0-9_]+")


def _tokens(text: str) -> set[str]:
    return {w.lower() for w in _WORD.findall(text or "") if len(w) > 2}


@dataclass
class Skill:
    name: str
    trigger: str  # short description of when this applies
    approach: str  # what worked
    acceptance_checks: list[str] = field(default_factory=list)
    uses: int = 0
    created_ts: float = field(default_factory=time.time)


class SkillStore:
    def __init__(self, skills_dir: Path):
        self.dir = Path(skills_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-").lower() or "skill"
        return self.dir / f"{safe}.json"

    def save(self, skill: Skill) -> Path:
        p = self._path(skill.name)
        p.write_text(json.dumps(asdict(skill), indent=2), encoding="utf-8")
        return p

    def all(self) -> list[Skill]:
        out = []
        for p in sorted(self.dir.glob("*.json")):
            try:
                out.append(Skill(**json.loads(p.read_text(encoding="utf-8"))))
            except (json.JSONDecodeError, TypeError):
                pass
        return out

    def recall(self, query: str, limit: int = 3) -> list[Skill]:
        q = _tokens(query)
        if not q:
            return []
        scored: list[tuple[float, Skill]] = []
        for s in self.all():
            text = f"{s.name} {s.trigger} {s.approach}"
            overlap = len(q & _tokens(text))
            if overlap:
                scored.append((overlap, s))
        scored.sort(key=lambda t: t[0], reverse=True)
        return [s for _, s in scored[:limit]]

    def record_use(self, name: str) -> None:
        p = self._path(name)
        if p.exists():
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                data["uses"] = int(data.get("uses", 0)) + 1
                p.write_text(json.dumps(data, indent=2), encoding="utf-8")
            except (json.JSONDecodeError, OSError):
                pass
