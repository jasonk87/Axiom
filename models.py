from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class Mode(str, Enum):
    CONVERSATION = "conversation"
    PLAN = "plan"
    IMPLEMENT = "implement"


class StepType(str, Enum):
    DISCOVERY = "discovery"
    EXECUTION = "execution"
    VERIFICATION = "verification"


class VerificationProfile(str, Enum):
    NONE = "none"
    BASIC = "basic"
    COMMANDS_ONLY = "commands_only"


class StepStatus(str, Enum):
    PENDING = "pending"
    COMPLETED = "completed"
    SKIPPED = "skipped"
    FAILED = "failed"


class ApprovalMode(str, Enum):
    NORMAL = "normal"
    PHASED = "phased"


class RunStatus(str, Enum):
    PREPARED = "prepared"
    AWAITING_DECOMPOSITION_APPROVAL = "awaiting_decomposition_approval"
    AWAITING_IMPLEMENTATION_APPROVAL = "awaiting_implementation_approval"
    AWAITING_PLAN_APPROVAL = "awaiting_plan_approval"
    AWAITING_PHASE_APPROVAL = "awaiting_phase_approval"
    AUTO_RUNNING_READ_ONLY_PHASE = "auto_running_read_only_phase"
    RUNNING = "running"
    CANCELLING = "cancelling"
    RECOVERED_AFTER_RESTART = "recovered_after_restart"
    CANCELLED = "cancelled"
    DECLINED_PLAN = "declined_plan"
    DECLINED_PHASE = "declined_phase"
    DECLINED_DECOMPOSITION = "declined_decomposition"
    DECLINED_IMPLEMENTATION = "declined_implementation"
    COMPLETED = "completed"
    FAILED = "failed"


class PhasePower(str, Enum):
    READ_ONLY = "read_only"
    WRITES_FILES = "writes_files"
    RUNS_COMMANDS = "runs_commands"
    VERIFY_ONLY = "verify_only"
    MIXED = "mixed"


class FailureCategory(str, Enum):
    COMMAND_EXECUTION_FAILURE = "command_execution_failure"
    VERIFICATION_FAILURE = "verification_failure"
    WRITE_POLICY_BLOCK = "write_policy_block"
    SCOPE_VIOLATION = "scope_violation"
    PROTECTED_PATH_VIOLATION = "protected_path_violation"
    PARSE_OR_ANALYSIS_FAILURE = "parse_or_analysis_failure"
    COMMAND_POLICY_BLOCK = "command_policy_block"
    UNKNOWN_FAILURE = "unknown_failure"


class CommandPolicyMode(str, Enum):
    PERMISSIVE = "permissive"
    SAFE = "safe"


@dataclass
class LLMValidationIssue:
    code: str
    message: str
    path: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class LLMActivityEvent:
    stage: str
    status: str
    summary: str
    attempt: int = 0
    details: dict[str, Any] = field(default_factory=dict)
    artifact_reference: ArtifactReference | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status,
            "summary": self.summary,
            "attempt": self.attempt,
            "details": self.details,
            "artifact_reference": (
                self.artifact_reference.to_dict() if self.artifact_reference else None
            ),
        }


@dataclass
class LLMStructuredResult:
    feature: str
    provider: str
    model: str
    enabled: bool
    accepted: bool
    fallback_used: bool
    attempts_used: int
    retry_limit: int
    final_message: str
    accepted_payload: dict[str, Any] | None = None
    events: list[LLMActivityEvent] = field(default_factory=list)
    artifact_references: list[ArtifactReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "feature": self.feature,
            "provider": self.provider,
            "model": self.model,
            "enabled": self.enabled,
            "accepted": self.accepted,
            "fallback_used": self.fallback_used,
            "attempts_used": self.attempts_used,
            "retry_limit": self.retry_limit,
            "final_message": self.final_message,
            "accepted_payload": self.accepted_payload,
            "events": [event.to_dict() for event in self.events],
            "artifact_references": [
                artifact.to_dict() for artifact in self.artifact_references
            ],
        }


@dataclass(frozen=True)
class PermissionSet:
    can_read_files: bool
    can_write_files: bool
    can_run_commands: bool


@dataclass
class ScopeConfig:
    raw_paths: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VerificationConfig:
    profile: VerificationProfile = VerificationProfile.NONE
    commands: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile.value,
            "commands": self.commands,
        }


@dataclass
class ArtifactReference:
    artifact_type: str
    label: str
    path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RepoIndexSummary:
    generated: bool
    total_files: int = 0
    top_level_directories: list[str] = field(default_factory=list)
    file_extensions: dict[str, int] = field(default_factory=dict)
    likely_entry_files: list[str] = field(default_factory=list)
    likely_config_files: list[str] = field(default_factory=list)
    likely_test_files: list[str] = field(default_factory=list)
    python_symbols: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    protected_files_indexed: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    semantic_search_results: list[dict[str, Any]] = field(default_factory=list)
    artifact_reference: ArtifactReference | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated": self.generated,
            "total_files": self.total_files,
            "top_level_directories": self.top_level_directories,
            "file_extensions": self.file_extensions,
            "likely_entry_files": self.likely_entry_files,
            "likely_config_files": self.likely_config_files,
            "likely_test_files": self.likely_test_files,
            "python_symbols": self.python_symbols,
            "protected_files_indexed": self.protected_files_indexed,
            "notes": self.notes,
            "semantic_search_results": self.semantic_search_results,
            "artifact_reference": (
                self.artifact_reference.to_dict() if self.artifact_reference else None
            ),
        }


@dataclass
class ProjectMemoryContext:
    summary: str
    known_commands: list[str] = field(default_factory=list)
    recent_context: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": self.summary,
            "known_commands": self.known_commands,
            "recent_context": self.recent_context,
        }


@dataclass
class TaskContextPackage:
    task: str
    mode: str
    approval_mode: str
    effective_scope: dict[str, Any]
    protected_paths: list[str]
    verification: dict[str, Any]
    command_policy: str
    project_memory: dict[str, Any] | None
    repo_index_summary: dict[str, Any] | None
    prior_generated_plan: dict[str, Any] | None
    phase_policies: list[dict[str, Any]] = field(default_factory=list)
    change_preview_summary: dict[str, Any] | None = None
    llm_summary: dict[str, Any] | None = None
    llm_review_summary: dict[str, Any] | None = None
    prior_phase_results: list[dict[str, Any]] = field(default_factory=list)
    prior_verification_outcomes: list[dict[str, Any]] = field(default_factory=list)
    artifact_references: list[ArtifactReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "mode": self.mode,
            "approval_mode": self.approval_mode,
            "effective_scope": self.effective_scope,
            "protected_paths": self.protected_paths,
            "verification": self.verification,
            "command_policy": self.command_policy,
            "project_memory": self.project_memory,
            "repo_index_summary": self.repo_index_summary,
            "prior_generated_plan": self.prior_generated_plan,
            "phase_policies": self.phase_policies,
            "change_preview_summary": self.change_preview_summary,
            "llm_summary": self.llm_summary,
            "llm_review_summary": self.llm_review_summary,
            "prior_phase_results": self.prior_phase_results,
            "prior_verification_outcomes": self.prior_verification_outcomes,
            "artifact_references": [
                artifact.to_dict() for artifact in self.artifact_references
            ],
        }


@dataclass
class PlanStep:
    id: str
    step_type: StepType
    title: str
    description: str
    dependencies: list[str]
    scope_hint: str
    expected_outcome: str
    phase: str = "execution"
    risk_hint: str = "low"
    approval_hint: str = "included_in_run_approval"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": self.step_type.value,
            "title": self.title,
            "description": self.description,
            "dependencies": self.dependencies,
            "scope_hint": self.scope_hint,
            "expected_outcome": self.expected_outcome,
            "phase": self.phase,
            "risk_hint": self.risk_hint,
            "approval_hint": self.approval_hint,
        }


@dataclass
class Plan:
    steps: list[PlanStep]

    def to_dict(self) -> dict[str, Any]:
        return {"steps": [step.to_dict() for step in self.steps]}


@dataclass
class CommandResult:
    command: str
    exit_code: int
    stdout: str
    stderr: str
    success: bool
    summary: str
    cancelled: bool = False
    artifact_reference: ArtifactReference | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "command": self.command,
            "exit_code": self.exit_code,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "success": self.success,
            "cancelled": self.cancelled,
            "summary": self.summary,
            "artifact_reference": (
                self.artifact_reference.to_dict() if self.artifact_reference else None
            ),
        }


@dataclass
class SnapshotReference:
    snapshot_id: str
    snapshot_path: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class BlockedAction:
    action: str
    path: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class StepResult:
    step_id: str
    title: str
    step_type: StepType
    status: StepStatus
    message: str
    phase: str = "execution"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "title": self.title,
            "step_type": self.step_type.value,
            "status": self.status.value,
            "message": self.message,
            "phase": self.phase,
            "details": self.details,
        }


@dataclass
class PhaseResult:
    phase: str
    status: StepStatus
    message: str
    classification: str | None = None
    approval_required: bool = True
    auto_ran: bool = False
    approval_reason: str = ""
    step_ids: list[str] = field(default_factory=list)
    verification_outcomes: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "status": self.status.value,
            "message": self.message,
            "classification": self.classification,
            "approval_required": self.approval_required,
            "auto_ran": self.auto_ran,
            "approval_reason": self.approval_reason,
            "step_ids": self.step_ids,
            "verification_outcomes": self.verification_outcomes,
        }


@dataclass
class PhasePolicy:
    phase: str
    classification: PhasePower
    approval_required: bool
    auto_run_allowed: bool
    reason: str
    auto_ran: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "phase": self.phase,
            "classification": self.classification.value,
            "approval_required": self.approval_required,
            "auto_run_allowed": self.auto_run_allowed,
            "reason": self.reason,
            "auto_ran": self.auto_ran,
        }


@dataclass
class FailureClassification:
    category: FailureCategory
    reason: str
    source: str
    artifact_reference: ArtifactReference | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "reason": self.reason,
            "source": self.source,
            "artifact_reference": (
                self.artifact_reference.to_dict() if self.artifact_reference else None
            ),
        }


@dataclass
class PreviewFileChange:
    path: str
    action: str
    blocked: bool
    summary: str
    before_exists: bool = False
    before_preview: str | None = None
    after_preview: str | None = None
    diff_preview: str | None = None
    origin: dict[str, Any] = field(default_factory=dict)
    content_hashes: dict[str, str | None] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "action": self.action,
            "blocked": self.blocked,
            "summary": self.summary,
            "before_exists": self.before_exists,
            "before_preview": self.before_preview,
            "after_preview": self.after_preview,
            "diff_preview": self.diff_preview,
            "origin": self.origin,
            "content_hashes": self.content_hashes,
        }


@dataclass
class ChangePreview:
    intended_file_writes: list[str] = field(default_factory=list)
    intended_commands: list[str] = field(default_factory=list)
    relevant_steps: list[str] = field(default_factory=list)
    file_changes: list[PreviewFileChange] = field(default_factory=list)
    blocked_writes: list[dict[str, str]] = field(default_factory=list)
    artifact_reference: ArtifactReference | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "intended_file_writes": self.intended_file_writes,
            "intended_commands": self.intended_commands,
            "relevant_steps": self.relevant_steps,
            "file_changes": [change.to_dict() for change in self.file_changes],
            "blocked_writes": self.blocked_writes,
            "artifact_reference": (
                self.artifact_reference.to_dict() if self.artifact_reference else None
            ),
        }


@dataclass
class RepairSummary:
    attempted: bool
    auto_repair_enabled: bool
    eligible: bool
    reason: str
    repair_plan: Plan | None = None
    repair_step_results: list[StepResult] = field(default_factory=list)
    repair_execution_result: ExecutionResult | None = None
    post_repair_failure_classification: FailureClassification | None = None
    artifact_references: list[ArtifactReference] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "auto_repair_enabled": self.auto_repair_enabled,
            "eligible": self.eligible,
            "reason": self.reason,
            "repair_plan": self.repair_plan.to_dict() if self.repair_plan else None,
            "repair_step_results": [
                step.to_dict() for step in self.repair_step_results
            ],
            "repair_execution_result": (
                self.repair_execution_result.to_dict()
                if self.repair_execution_result
                else None
            ),
            "post_repair_failure_classification": (
                self.post_repair_failure_classification.to_dict()
                if self.post_repair_failure_classification
                else None
            ),
            "artifact_references": [
                artifact.to_dict() for artifact in self.artifact_references
            ],
        }


class TaskAction(str, Enum):
    ANALYZE = "analyze"
    CREATE_FILE = "create_file"
    MODIFY_FILE = "modify_file"
    RUN_COMMAND = "run_command"
    RESTORE_SNAPSHOT = "restore_snapshot"
    COMPLEX = "complex"
    UNKNOWN = "unknown"


@dataclass
class SubTask:
    action: str
    description: str
    target_path: str | None = None
    command: str | None = None
    result_summary: str | None = None
    dependencies: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskInterpretation:
    raw_task: str
    summary: str
    action: TaskAction
    target_path: str | None = None
    content: str | None = None
    command: str | None = None
    snapshot_id: str | None = None
    subtasks: list[SubTask] | None = None
    compressed_history: str | None = None
    compressed_subtask_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if self.subtasks:
            d["subtasks"] = [st.to_dict() for st in self.subtasks]
        return d


@dataclass
class ExecutionResult:
    success: bool
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskResult:
    task_interpretation: TaskInterpretation
    selected_mode: Mode
    permissions: PermissionSet
    approval_mode: ApprovalMode
    command_policy: CommandPolicyMode
    effective_scope: dict[str, Any]
    protected_paths: list[str]
    verification: VerificationConfig
    context_package: TaskContextPackage | None
    repo_index_summary: RepoIndexSummary | None
    change_preview: ChangePreview | None
    llm_summary: LLMStructuredResult | None
    llm_review_summary: LLMStructuredResult | None
    plan: Plan | None
    phase_policies: list[PhasePolicy]
    phase_results: list[PhaseResult]
    step_results: list[StepResult]
    blocked_actions: list[BlockedAction]
    files_read: list[str]
    files_modified: list[str]
    commands_run: list[CommandResult]
    artifact_references: list[ArtifactReference]
    initial_execution_result: ExecutionResult | None
    failure_classification: FailureClassification | None
    repair_summary: RepairSummary | None
    final_execution_result: ExecutionResult
    execution_result: ExecutionResult
    snapshot_reference: SnapshotReference | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_interpretation": self.task_interpretation.to_dict(),
            "selected_mode": self.selected_mode.value,
            "permissions": asdict(self.permissions),
            "approval_mode": self.approval_mode.value,
            "command_policy": self.command_policy.value,
            "effective_scope": self.effective_scope,
            "protected_paths": self.protected_paths,
            "verification": self.verification.to_dict(),
            "context_package": (
                self.context_package.to_dict() if self.context_package else None
            ),
            "repo_index_summary": (
                self.repo_index_summary.to_dict() if self.repo_index_summary else None
            ),
            "change_preview": (
                self.change_preview.to_dict() if self.change_preview else None
            ),
            "llm_summary": self.llm_summary.to_dict() if self.llm_summary else None,
            "llm_review_summary": (
                self.llm_review_summary.to_dict() if self.llm_review_summary else None
            ),
            "plan": self.plan.to_dict() if self.plan else None,
            "phase_policies": [policy.to_dict() for policy in self.phase_policies],
            "phase_results": [phase.to_dict() for phase in self.phase_results],
            "step_results": [step.to_dict() for step in self.step_results],
            "blocked_actions": [action.to_dict() for action in self.blocked_actions],
            "files_read": self.files_read,
            "files_modified": self.files_modified,
            "commands_run": [command.to_dict() for command in self.commands_run],
            "artifact_references": [
                artifact.to_dict() for artifact in self.artifact_references
            ],
            "initial_execution_result": (
                self.initial_execution_result.to_dict()
                if self.initial_execution_result
                else None
            ),
            "failure_classification": (
                self.failure_classification.to_dict()
                if self.failure_classification
                else None
            ),
            "repair_summary": (
                self.repair_summary.to_dict() if self.repair_summary else None
            ),
            "final_execution_result": self.final_execution_result.to_dict(),
            "execution_result": self.execution_result.to_dict(),
            "snapshot_reference": (
                self.snapshot_reference.to_dict() if self.snapshot_reference else None
            ),
        }
