from __future__ import annotations

import re
from dataclasses import dataclass

from models import CommandPolicyMode


@dataclass
class CommandPolicyDecision:
    allowed: bool
    reason: str
    matched_rule: str | None = None


class CommandPolicyError(RuntimeError):
    pass


class CommandPolicy:
    SAFE_RULES = [
        ("destructive_remove_unix", re.compile(r"\brm\s+-rf\b", re.IGNORECASE)),
        (
            "destructive_remove_powershell",
            re.compile(r"\bRemove-Item\b.*\b-Recurse\b.*\b-Force\b", re.IGNORECASE),
        ),
        (
            "destructive_remove_cmd",
            re.compile(r"\b(?:del|erase|rd|rmdir)\b.*\b(?:/s|/q)\b", re.IGNORECASE),
        ),
        (
            "broad_permission_change",
            re.compile(r"\b(?:chmod|chown|icacls)\b", re.IGNORECASE),
        ),
        (
            "system_shutdown",
            re.compile(
                r"\b(?:shutdown|reboot|halt|poweroff|Restart-Computer|Stop-Computer)\b",
                re.IGNORECASE,
            ),
        ),
    ]

    def __init__(self, mode: CommandPolicyMode) -> None:
        self.mode = mode

    def evaluate(self, command: str) -> CommandPolicyDecision:
        if self.mode == CommandPolicyMode.PERMISSIVE:
            return CommandPolicyDecision(
                allowed=True, reason="Permissive policy allows command execution."
            )

        for rule_name, pattern in self.SAFE_RULES:
            if pattern.search(command):
                return CommandPolicyDecision(
                    allowed=False,
                    reason=f"Safe command policy blocked the command because it matched rule '{rule_name}'.",
                    matched_rule=rule_name,
                )

        return CommandPolicyDecision(
            allowed=True, reason="Safe policy allowed the command."
        )
