"""Procedural memory / skills (Hermes-style learning loop).

After a successful run, sidekick distills a lightweight, reusable "skill" — a named recipe
(task pattern -> approach + acceptance checks that worked) stored as JSON. On a new task,
recall() does a simple keyword match to surface relevant prior skills, which the planner
can use as hints. This is the minimal version of Hermes' "creates skills from experience"
loop, with FTS approximated by token-overlap scoring.

Skill security scanning (ported from Hermes 0.17's "Skills Hub overhaul with security
scanning"): a recalled skill's `approach` is fed back into agent prompts and its
`acceptance_checks` are executed as shell (`run_checks` uses shell=True), so a poisoned
skill is a real injection vector. `scan_skill()` flags dangerous shell patterns; `save()`
refuses unsafe skills by default and `recall()` never surfaces one that fails the scan.
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


# Dangerous shell patterns checked before a skill is trusted. Each entry is
# (compiled regex, human-readable reason). Conservative by design: these target
# destructive, remote-exec, privilege-escalation, and exfiltration shapes that should
# never appear in a benign build/test/lint recipe.
_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\brm\s+-[a-z]*r[a-z]*f|\brm\s+-[a-z]*f[a-z]*r", re.I), "recursive force-delete (rm -rf)"),
    (re.compile(r"\b(curl|wget)\b[^\n|]*\|\s*(sudo\s+)?(ba|z|da|)?sh\b", re.I), "pipe remote download into a shell"),
    (re.compile(r"\bsudo\b", re.I), "privilege escalation (sudo)"),
    (re.compile(r":\(\)\s*\{.*\|.*&\s*\}\s*;", re.S), "fork bomb"),
    (re.compile(r"\bbase64\b[^\n|]*-d[^\n|]*\|\s*(ba|z|)?sh\b", re.I), "decode-and-execute (base64 | sh)"),
    (re.compile(r"\b(nc|ncat|netcat)\b\s+[^\n]*-e", re.I), "reverse shell (netcat -e)"),
    (re.compile(r"\beval\b[^\n]*\$\((?:curl|wget)", re.I), "eval of remote output"),
    (re.compile(r">\s*/dev/sd[a-z]\b", re.I), "raw write to a block device"),
    (re.compile(r"(\.ssh/|/etc/(passwd|shadow|sudoers)|AWS_SECRET|_API_KEY|\.env\b)[^\n]*\|\s*(curl|wget|nc)", re.I),
     "exfiltrate secrets over the network"),
    (re.compile(r"\bgit\s+push\b[^\n]*--force|\bgit\s+push\b[^\n]*\s-f\b", re.I), "force-push"),
]


class UnsafeSkillError(ValueError):
    """Raised when a skill fails the security scan and is refused."""

    def __init__(self, name: str, findings: list[str]):
        self.findings = findings
        super().__init__(f"unsafe skill {name!r}: {'; '.join(findings)}")


def scan_skill(skill: Skill) -> list[str]:
    """Return a list of human-readable security findings for a skill (empty == clean)."""
    text = "\n".join([skill.name, skill.trigger, skill.approach, *skill.acceptance_checks])
    return [reason for rx, reason in _DANGEROUS_PATTERNS if rx.search(text)]


@dataclass
class Skill:
    name: str
    trigger: str  # short description of when this applies
    approach: str  # what worked
    acceptance_checks: list[str] = field(default_factory=list)
    uses: int = 0
    created_ts: float = field(default_factory=time.time)
    # Security-scan findings recorded at save time; non-empty means quarantined.
    findings: list[str] = field(default_factory=list)


class SkillStore:
    def __init__(self, skills_dir: Path):
        self.dir = Path(skills_dir)
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, name: str) -> Path:
        safe = re.sub(r"[^a-zA-Z0-9_-]+", "-", name).strip("-").lower() or "skill"
        return self.dir / f"{safe}.json"

    def save(self, skill: Skill, allow_unsafe: bool = False) -> Path:
        """Persist a skill after a security scan.

        Raises UnsafeSkillError if the skill contains dangerous shell patterns, unless
        `allow_unsafe=True` (in which case it is stored with `findings` recorded so recall
        can still skip it).
        """
        findings = scan_skill(skill)
        if findings and not allow_unsafe:
            raise UnsafeSkillError(skill.name, findings)
        skill.findings = findings
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
            # Never surface a quarantined/unsafe skill back into prompts or checks — guard
            # both stored findings and any skill dropped into the dir out-of-band.
            if s.findings or scan_skill(s):
                continue
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
