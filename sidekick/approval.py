"""Auto-approval policy (Raschka #3: bounded, structured tool use).

Translates a high-level approval *level* into concrete Claude Code headless flags:
permission mode plus an explicit tool allowlist. The default level auto-approves edits
and a scoped set of build/test/lint/vcs Bash commands, while still refusing unlisted or
dangerous operations — the speed of full autonomy without an open shell grant.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import (
    APPROVAL_ACCEPT_EDITS_ALLOWLIST,
    APPROVAL_BYPASS,
    APPROVAL_EDITS_NO_BASH,
    DEFAULT_BASH_ALLOWLIST,
    DEFAULT_EDIT_TOOLS,
)


@dataclass
class ApprovalPolicy:
    level: str = APPROVAL_ACCEPT_EDITS_ALLOWLIST

    @property
    def permission_mode(self) -> str:
        if self.level == APPROVAL_BYPASS:
            return "bypassPermissions"
        return "acceptEdits"

    @property
    def requires_dangerous_flag(self) -> bool:
        return self.level == APPROVAL_BYPASS

    def allowed_tools(self) -> list[str]:
        """Explicit allowlist passed via --allowedTools.

        Empty list under bypass (everything is allowed by the permission mode itself).
        """
        if self.level == APPROVAL_BYPASS:
            return []
        tools = list(DEFAULT_EDIT_TOOLS)
        if self.level == APPROVAL_ACCEPT_EDITS_ALLOWLIST:
            tools += list(DEFAULT_BASH_ALLOWLIST)
        # APPROVAL_EDITS_NO_BASH: edit tools only, no Bash grants.
        return tools

    def disallowed_tools(self) -> list[str]:
        if self.level == APPROVAL_EDITS_NO_BASH:
            return ["Bash"]
        return []

    @property
    def can_run_bash(self) -> bool:
        return self.level != APPROVAL_EDITS_NO_BASH

    def describe(self) -> str:
        return {
            APPROVAL_ACCEPT_EDITS_ALLOWLIST: "acceptEdits + scoped tool allowlist",
            APPROVAL_BYPASS: "bypassPermissions (all tools auto-run)",
            APPROVAL_EDITS_NO_BASH: "acceptEdits, edits only (no Bash)",
        }.get(self.level, self.level)
