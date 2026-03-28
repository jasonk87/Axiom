from __future__ import annotations

import subprocess
import time

from artifact_manager import ArtifactManager
from command_policy import CommandPolicy, CommandPolicyError
from models import CommandResult, CommandPolicyMode, PermissionSet
from permission_manager import PermissionManager


class TerminalRunner:
    PREVIEW_LIMIT = 800

    def __init__(
        self,
        project_root: str,
        permissions: PermissionSet,
        command_policy_mode: CommandPolicyMode,
        artifact_manager: ArtifactManager | None = None,
    ) -> None:
        self.project_root = project_root
        self.permissions = permissions
        self.command_policy = CommandPolicy(command_policy_mode)
        self.artifact_manager = artifact_manager
        self.commands_run: list[CommandResult] = []

    def run(self, command: str, cancellation_event=None) -> CommandResult:
        PermissionManager.require_command(self.permissions)
        decision = self.command_policy.evaluate(command)
        if not decision.allowed:
            raise CommandPolicyError(
                f"Command policy blocked '{command}'. {decision.reason}"
            )
        process = subprocess.Popen(
            command,
            cwd=self.project_root,
            shell=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        cancelled = False
        while process.poll() is None:
            if cancellation_event is not None and cancellation_event.is_set():
                cancelled = True
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=2)
                break
            time.sleep(0.1)
        stdout, stderr = process.communicate()
        exit_code = process.returncode if process.returncode is not None else -1
        artifact_reference = None
        if self.artifact_manager is not None:
            artifact_reference = self.artifact_manager.save_json(
                self.artifact_manager.build_filename("command_log"),
                {
                    "command": command,
                    "exit_code": exit_code,
                    "stdout": stdout,
                    "stderr": stderr,
                    "success": exit_code == 0 and not cancelled,
                    "cancelled": cancelled,
                },
            )

        result = CommandResult(
            command=command,
            exit_code=exit_code,
            stdout=self._preview(stdout),
            stderr=self._preview(stderr),
            success=exit_code == 0 and not cancelled,
            cancelled=cancelled,
            summary=self._build_summary(command, exit_code, stdout, stderr, cancelled),
            artifact_reference=artifact_reference,
        )
        self.commands_run.append(result)
        return result

    def _preview(self, text: str) -> str:
        if len(text) <= self.PREVIEW_LIMIT:
            return text
        return f"{text[: self.PREVIEW_LIMIT]}... [truncated]"

    @staticmethod
    def _build_summary(command: str, exit_code: int, stdout: str, stderr: str, cancelled: bool) -> str:
        if cancelled:
            return f"Command cancelled: {command}."
        if exit_code == 0:
            first_line = next((line.strip() for line in stdout.splitlines() if line.strip()), "")
            if first_line:
                return f"Command succeeded: {command}. First output line: {first_line}"
            return f"Command succeeded: {command}."

        first_error = next((line.strip() for line in stderr.splitlines() if line.strip()), "")
        if first_error:
            return f"Command failed: {command}. First error line: {first_error}"
        return f"Command failed: {command} with exit code {exit_code}."
