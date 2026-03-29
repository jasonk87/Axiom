from __future__ import annotations

import argparse

from models import (
    ApprovalMode,
    CommandPolicyMode,
    VerificationConfig,
    VerificationProfile,
)
from mode_manager import ModeManager
from orchestrator import Orchestrator, format_result


def parse_csv_list(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Axiom: a gated local-first coding workbench."
    )
    parser.add_argument(
        "--project", required=True, help="Path to the local project workspace."
    )
    parser.add_argument(
        "--mode",
        required=True,
        choices=["conversation", "plan", "implement"],
        help="Operating mode for the task.",
    )
    parser.add_argument("--task", required=True, help="Task to interpret and handle.")
    parser.add_argument(
        "--scope",
        help="Optional comma-separated list of files or folders that define the task scope.",
    )
    parser.add_argument(
        "--protect",
        help="Optional comma-separated list of files or folders that are always write-protected.",
    )
    parser.add_argument(
        "--verify-profile",
        default="none",
        choices=["none", "basic", "commands_only"],
        help="Post-execution verification profile.",
    )
    parser.add_argument(
        "--verify-command",
        action="append",
        default=[],
        help="Verification command to run after execution. Repeat to provide multiple commands.",
    )
    parser.add_argument(
        "--build-repo-index",
        action="store_true",
        help="Build a lightweight read-only repo index to improve planning and reporting for this run.",
    )
    parser.add_argument(
        "--command-policy",
        default="permissive",
        choices=["permissive", "safe"],
        help="Command policy mode for IMPLEMENT command execution.",
    )
    parser.add_argument(
        "--preview-changes",
        action="store_true",
        help="Show a simple change preview before plan approval in IMPLEMENT mode.",
    )
    parser.add_argument(
        "--auto-repair",
        default="no",
        choices=["yes", "no"],
        help="Allow at most one automatic repair attempt after an IMPLEMENT failure.",
    )
    parser.add_argument(
        "--approval-mode",
        default="normal",
        choices=["normal", "phased"],
        help="Approval behavior for IMPLEMENT runs.",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    mode = ModeManager.parse_mode(args.mode)
    orchestrator = Orchestrator(args.project)
    verification = VerificationConfig(
        profile=VerificationProfile(args.verify_profile),
        commands=args.verify_command,
    )
    result = orchestrator.run(
        mode=mode,
        task=args.task,
        scope_paths=parse_csv_list(args.scope),
        protected_paths=parse_csv_list(args.protect),
        verification_config=verification,
        build_repo_index=args.build_repo_index,
        command_policy=CommandPolicyMode(args.command_policy),
        preview_changes=args.preview_changes,
        auto_repair=args.auto_repair == "yes",
        approval_mode=ApprovalMode(args.approval_mode),
    )
    print(format_result(result))


if __name__ == "__main__":
    main()
