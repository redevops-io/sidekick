"""sidekick — local coding-agent orchestrator.

Fans out a high-level coding task to multiple auto-approved Claude Code headless
sessions, each isolated on its own git worktree/branch, with live progress and
measured speed/accuracy objectives.

Design lineage:
  * Nous Hermes-Agent — skills/memory loop, pluggable execution backends, RPC subagents.
  * Sebastian Raschka, "The Six Components of a Coding Agent" — live repo context,
    cache-shaped prompts, structured/bounded tool use, context-bloat control,
    structured session memory, bounded subagent delegation.
"""

__version__ = "0.1.0"
