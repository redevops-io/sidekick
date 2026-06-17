"""Context-bloat control (Raschka #4).

Two pure strategies:
  * clip() — truncate any single verbose item to a fixed budget, keeping head + tail.
  * reduce_transcript() — tiered compression: keep recent entries at full fidelity,
    summarize older ones aggressively, and deduplicate repeated file reads.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


def clip(text: str, max_chars: int = 4000) -> str:
    """Clip text to max_chars, preserving the head and tail with an elision marker."""
    if text is None:
        return ""
    if len(text) <= max_chars:
        return text
    head = max_chars * 2 // 3
    tail = max_chars - head
    omitted = len(text) - head - tail
    return f"{text[:head]}\n... [clipped {omitted} chars] ...\n{text[-tail:]}"


@dataclass
class TranscriptEntry:
    role: str  # "assistant" | "tool" | "user" | "note"
    summary: str
    detail: str = ""
    dedup_key: str | None = None  # e.g. a file path, to drop repeated reads


def reduce_transcript(
    entries: Sequence[TranscriptEntry],
    keep_recent: int = 12,
    clip_old: int = 200,
    clip_recent: int = 2000,
) -> list[TranscriptEntry]:
    """Tiered transcript reduction.

    Recent `keep_recent` entries are clipped lightly; older entries are clipped hard.
    Repeated entries sharing a dedup_key keep only their most recent occurrence (older
    duplicate file reads collapse to a one-line stub).
    """
    n = len(entries)
    # Identify the last index per dedup_key so earlier duplicates can be stubbed.
    last_index: dict[str, int] = {}
    for i, e in enumerate(entries):
        if e.dedup_key:
            last_index[e.dedup_key] = i

    out: list[TranscriptEntry] = []
    for i, e in enumerate(entries):
        is_recent = i >= n - keep_recent
        if e.dedup_key and last_index.get(e.dedup_key) != i:
            out.append(
                TranscriptEntry(
                    role=e.role,
                    summary=f"[superseded] {clip(e.summary, 80)}",
                    detail="",
                    dedup_key=e.dedup_key,
                )
            )
            continue
        budget = clip_recent if is_recent else clip_old
        out.append(
            TranscriptEntry(
                role=e.role,
                summary=clip(e.summary, budget),
                detail=clip(e.detail, budget) if is_recent else "",
                dedup_key=e.dedup_key,
            )
        )
    return out


def render_transcript(entries: Sequence[TranscriptEntry]) -> str:
    lines = []
    for e in entries:
        lines.append(f"[{e.role}] {e.summary}")
        if e.detail:
            lines.append(e.detail)
    return "\n".join(lines)
