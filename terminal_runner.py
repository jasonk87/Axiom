from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass
from queue import Empty, Queue

from artifact_manager import ArtifactManager
from command_policy import CommandPolicy, CommandPolicyError
from models import CommandResult, CommandPolicyMode, PermissionSet
from permission_manager import PermissionManager


@dataclass
class BackgroundProcess:
    id: str
    process: subprocess.Popen
    command: str
    stdout_queue: Queue[str]
    stderr_queue: Queue[str]
    stdout_thread: threading.Thread
    stderr_thread: threading.Thread
    stdout_history: list[str]
    stderr_history: list[str]

    def is_running(self) -> bool:
        return self.process.poll() is None


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
        self.background_processes: dict[str, BackgroundProcess] = {}

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
    def _build_summary(
        command: str, exit_code: int, stdout: str, stderr: str, cancelled: bool
    ) -> str:
        if cancelled:
            return f"Command cancelled: {command}."
        if exit_code == 0:
            first_line = next(
                (line.strip() for line in stdout.splitlines() if line.strip()), ""
            )
            if first_line:
                return f"Command succeeded: {command}. First output line: {first_line}"
            return f"Command succeeded: {command}."

        first_error = next(
            (line.strip() for line in stderr.splitlines() if line.strip()), ""
        )
        if first_error:
            return f"Command failed: {command}. First error line: {first_error}"
        return f"Command failed: {command} with exit code {exit_code}."

    def start_background_process(self, command: str, process_id: str) -> None:
        PermissionManager.require_command(self.permissions)
        decision = self.command_policy.evaluate(command)
        if not decision.allowed:
            raise CommandPolicyError(
                f"Command policy blocked '{command}'. {decision.reason}"
            )

        if process_id in self.background_processes:
            raise ValueError(f"Process with id {process_id} already exists.")

        process = subprocess.Popen(
            command,
            cwd=self.project_root,
            shell=True,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1, # Line buffered
        )

        stdout_queue: Queue[str] = Queue()
        stderr_queue: Queue[str] = Queue()
        stdout_history: list[str] = []
        stderr_history: list[str] = []

        def enqueue_output(out, queue, history):
            for line in iter(out.readline, ''):
                queue.put(line)
                history.append(line)
            out.close()

        stdout_thread = threading.Thread(target=enqueue_output, args=(process.stdout, stdout_queue, stdout_history), daemon=True)
        stderr_thread = threading.Thread(target=enqueue_output, args=(process.stderr, stderr_queue, stderr_history), daemon=True)
        stdout_thread.start()
        stderr_thread.start()

        self.background_processes[process_id] = BackgroundProcess(
            id=process_id,
            process=process,
            command=command,
            stdout_queue=stdout_queue,
            stderr_queue=stderr_queue,
            stdout_thread=stdout_thread,
            stderr_thread=stderr_thread,
            stdout_history=stdout_history,
            stderr_history=stderr_history,
        )

    def read_background_output(self, process_id: str, timeout: float = 0.5) -> tuple[str, str]:
        if process_id not in self.background_processes:
            raise ValueError(f"Process {process_id} not found.")

        bg_process = self.background_processes[process_id]

        stdout_lines = []
        stderr_lines = []

        # Read available stdout
        while True:
            try:
                line = bg_process.stdout_queue.get(timeout=timeout if not stdout_lines else 0.1)
                stdout_lines.append(line)
            except Empty:
                break

        # Read available stderr
        while True:
            try:
                line = bg_process.stderr_queue.get(timeout=timeout if not stderr_lines else 0.1)
                stderr_lines.append(line)
            except Empty:
                break

        return "".join(stdout_lines), "".join(stderr_lines)

    def send_background_input(self, process_id: str, input_str: str) -> None:
        if process_id not in self.background_processes:
            raise ValueError(f"Process {process_id} not found.")

        bg_process = self.background_processes[process_id]
        if not bg_process.is_running():
            raise RuntimeError(f"Process {process_id} is not running.")

        if bg_process.process.stdin:
            bg_process.process.stdin.write(input_str)
            if not input_str.endswith('\n'):
                bg_process.process.stdin.write('\n')
            bg_process.process.stdin.flush()

    def kill_background_process(self, process_id: str) -> None:
        if process_id not in self.background_processes:
            raise ValueError(f"Process {process_id} not found.")

        bg_process = self.background_processes[process_id]
        if bg_process.is_running():
            bg_process.process.terminate()
            try:
                bg_process.process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                bg_process.process.kill()
                bg_process.process.wait(timeout=2)

        del self.background_processes[process_id]
