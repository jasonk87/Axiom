from __future__ import annotations


from models import CommandResult, VerificationConfig
from terminal_runner import TerminalRunner
from workspace_manager import WorkspaceManager


class VerificationManager:
    def __init__(
        self,
        verification_config: VerificationConfig,
        workspace: WorkspaceManager,
        terminal_runner: TerminalRunner,
    ) -> None:
        self.verification_config = verification_config
        self.workspace = workspace
        self.terminal_runner = terminal_runner

    def verify_file_write(
        self,
        relative_path: str,
        expected_content: str,
        cancellation_event=None,
    ) -> dict[str, object]:
        if cancellation_event is not None and cancellation_event.is_set():
            raise RuntimeError(
                "Verification was cancelled before file verification began."
            )
        target = self.workspace.resolve_path(relative_path)
        exists = target.exists()
        actual_content = self.workspace.read_text(relative_path) if exists else ""
        result: dict[str, object] = {
            "path": relative_path,
            "exists": exists,
            "content_matches": exists and actual_content == expected_content,
            "actual_content": actual_content,
            "configured_verification_commands": [],
        }
        if self.verification_config.commands:
            command_results = self.run_commands(cancellation_event=cancellation_event)
            result["configured_verification_commands"] = [
                command.to_dict() for command in command_results
            ]
            result["configured_commands_passed"] = all(
                command.success for command in command_results
            )
        return result

    def verify_command_success(
        self,
        command_result: CommandResult,
        cancellation_event=None,
    ) -> dict[str, object]:
        if cancellation_event is not None and cancellation_event.is_set():
            raise RuntimeError(
                "Verification was cancelled before command verification began."
            )
        result: dict[str, object] = {
            "command_success": command_result.success,
            "command_summary": command_result.summary,
            "configured_verification_commands": [],
        }
        if self.verification_config.commands:
            command_results = self.run_commands(cancellation_event=cancellation_event)
            result["configured_verification_commands"] = [
                command.to_dict() for command in command_results
            ]
            result["configured_commands_passed"] = all(
                command.success for command in command_results
            )
        return result

    def run_commands(self, cancellation_event=None) -> list[CommandResult]:
        if not self.verification_config.commands:
            return []
        results: list[CommandResult] = []
        for command in self.verification_config.commands:
            if cancellation_event is not None and cancellation_event.is_set():
                raise RuntimeError("Verification command execution was cancelled.")
            results.append(
                self.terminal_runner.run(command, cancellation_event=cancellation_event)
            )
        return results
