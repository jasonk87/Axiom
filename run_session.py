from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from threading import Event, Lock, Thread
import time
from uuid import uuid4

from artifact_manager import ArtifactManager
from llm_client import LLMSettings
from llm_settings_manager import LLMSettingsManager
from models import (
    ApprovalMode,
    ArtifactReference,
    BlockedAction,
    ChangePreview,
    CommandPolicyMode,
    CommandResult,
    ExecutionResult,
    LLMActivityEvent,
    LLMStructuredResult,
    Mode,
    PermissionSet,
    PhasePolicy,
    PhasePower,
    Plan,
    PlanStep,
    ProjectMemoryContext,
    PreviewFileChange,
    RepoIndexSummary,
    RunStatus,
    SnapshotReference,
    StepResult,
    StepStatus,
    StepType,
    SubTask,
    TaskAction,
    TaskInterpretation,
    VerificationConfig,
    VerificationProfile,
)
from llm_compress_service import LocalLLMCompressService
from llm_decompose_service import LocalLLMDecomposeService
from llm_implement_service import LocalLLMImplementService
from llm_replan_service import LocalLLMReplanService
from orchestrator import Orchestrator
from phase_policy import classify_plan_phases
from permission_manager import PermissionManager
from preview_manager import PreviewManager
from project_manager import ProjectManager
from scope_manager import ScopeManager
from snapshot_manager import SnapshotManager
from terminal_runner import TerminalRunner
from verification_manager import VerificationManager
from workspace_manager import WorkspaceManager
from vector_store import LocalVectorStore

ACTIVE_RUN_STATUSES = {
    RunStatus.RUNNING.value,
    RunStatus.AUTO_RUNNING_READ_ONLY_PHASE.value,
    RunStatus.CANCELLING.value,
}

TERMINAL_RUN_STATUSES = {
    RunStatus.RECOVERED_AFTER_RESTART.value,
    RunStatus.CANCELLED.value,
    RunStatus.DECLINED_PLAN.value,
    RunStatus.DECLINED_PHASE.value,
    RunStatus.DECLINED_DECOMPOSITION.value,
    RunStatus.DECLINED_IMPLEMENTATION.value,
    RunStatus.COMPLETED.value,
    RunStatus.FAILED.value,
}

PERSISTENCE_LOCK = Lock()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def plan_from_dict(payload: dict | None) -> Plan | None:
    if payload is None:
        return None
    return Plan(
        steps=[
            PlanStep(
                id=step["id"],
                step_type=StepType(step["type"]),
                title=step["title"],
                description=step["description"],
                dependencies=list(step["dependencies"]),
                scope_hint=step["scope_hint"],
                expected_outcome=step["expected_outcome"],
                phase=step.get("phase", "execution"),
                risk_hint=step.get("risk_hint", "low"),
                approval_hint=step.get("approval_hint", "included_in_run_approval"),
            )
            for step in payload.get("steps", [])
        ]
    )


def interpretation_from_dict(payload: dict) -> TaskInterpretation:
    return TaskInterpretation(
        raw_task=payload["raw_task"],
        summary=payload["summary"],
        action=TaskAction(payload["action"]),
        target_path=payload.get("target_path"),
        content=payload.get("content"),
        command=payload.get("command"),
        snapshot_id=payload.get("snapshot_id"),
        subtasks=(
            [
                SubTask(
                    action=st["action"],
                    description=st["description"],
                    target_path=st.get("target_path"),
                    command=st.get("command"),
                    result_summary=st.get("result_summary"),
                    dependencies=st.get("dependencies", []),
                )
                for st in payload["subtasks"]
            ]
            if payload.get("subtasks") is not None
            else None
        ),
        compressed_history=payload.get("compressed_history"),
        compressed_subtask_count=payload.get("compressed_subtask_count", 0),
    )


def verification_from_dict(payload: dict) -> VerificationConfig:
    return VerificationConfig(
        profile=VerificationProfile(payload["profile"]),
        commands=list(payload.get("commands", [])),
    )


def command_result_from_dict(payload: dict) -> CommandResult:
    artifact = payload.get("artifact_reference")
    return CommandResult(
        command=payload["command"],
        exit_code=payload["exit_code"],
        stdout=payload["stdout"],
        stderr=payload["stderr"],
        success=payload["success"],
        summary=payload["summary"],
        cancelled=payload.get("cancelled", False),
        artifact_reference=ArtifactReference(**artifact) if artifact else None,
    )


def blocked_action_from_dict(payload: dict) -> BlockedAction:
    return BlockedAction(
        action=payload["action"],
        path=payload["path"],
        reason=payload["reason"],
    )


def phase_policy_from_dict(payload: dict) -> PhasePolicy:
    return PhasePolicy(
        phase=payload["phase"],
        classification=PhasePower(payload["classification"]),
        approval_required=payload["approval_required"],
        auto_run_allowed=payload["auto_run_allowed"],
        reason=payload["reason"],
        auto_ran=payload.get("auto_ran", False),
    )


def repo_index_summary_from_dict(payload: dict | None) -> RepoIndexSummary | None:
    if payload is None:
        return None
    artifact = payload.get("artifact_reference")
    return RepoIndexSummary(
        generated=payload["generated"],
        total_files=payload.get("total_files", 0),
        top_level_directories=list(payload.get("top_level_directories", [])),
        file_extensions=dict(payload.get("file_extensions", {})),
        likely_entry_files=list(payload.get("likely_entry_files", [])),
        likely_config_files=list(payload.get("likely_config_files", [])),
        likely_test_files=list(payload.get("likely_test_files", [])),
        python_symbols=dict(payload.get("python_symbols", {})),
        protected_files_indexed=list(payload.get("protected_files_indexed", [])),
        notes=list(payload.get("notes", [])),
        semantic_search_results=list(payload.get("semantic_search_results", [])),
        artifact_reference=ArtifactReference(**artifact) if artifact else None,
    )


def change_preview_from_dict(payload: dict | None) -> ChangePreview | None:
    if payload is None:
        return None
    artifact = payload.get("artifact_reference")
    return ChangePreview(
        intended_file_writes=list(payload.get("intended_file_writes", [])),
        intended_commands=list(payload.get("intended_commands", [])),
        relevant_steps=list(payload.get("relevant_steps", [])),
        file_changes=[
            PreviewFileChange(
                path=change["path"],
                action=change["action"],
                blocked=change["blocked"],
                summary=change["summary"],
                before_exists=change.get("before_exists", False),
                before_preview=change.get("before_preview"),
                after_preview=change.get("after_preview"),
                diff_preview=change.get("diff_preview"),
                origin=dict(change.get("origin", {})),
                content_hashes=dict(change.get("content_hashes", {})),
            )
            for change in payload.get("file_changes", [])
        ],
        blocked_writes=list(payload.get("blocked_writes", [])),
        artifact_reference=ArtifactReference(**artifact) if artifact else None,
    )


def step_result_from_dict(payload: dict) -> StepResult:
    return StepResult(
        step_id=payload["step_id"],
        title=payload["title"],
        step_type=StepType(payload["step_type"]),
        status=StepStatus(payload["status"]),
        message=payload["message"],
        phase=payload.get("phase", "execution"),
        details=payload.get("details", {}),
    )


def llm_summary_from_dict(payload: dict | None) -> LLMStructuredResult | None:
    if payload is None:
        return None
    return LLMStructuredResult(
        feature=payload["feature"],
        provider=payload["provider"],
        model=payload["model"],
        enabled=payload["enabled"],
        accepted=payload["accepted"],
        fallback_used=payload["fallback_used"],
        attempts_used=payload["attempts_used"],
        retry_limit=payload["retry_limit"],
        final_message=payload["final_message"],
        accepted_payload=payload.get("accepted_payload"),
        events=[
            LLMActivityEvent(
                stage=event["stage"],
                status=event["status"],
                summary=event["summary"],
                attempt=event.get("attempt", 0),
                details=event.get("details", {}),
                artifact_reference=(
                    ArtifactReference(**event["artifact_reference"])
                    if event.get("artifact_reference")
                    else None
                ),
            )
            for event in payload.get("events", [])
        ],
        artifact_references=[
            ArtifactReference(**artifact)
            for artifact in payload.get("artifact_references", [])
        ],
    )


def project_memory_from_dict(payload: dict | None) -> ProjectMemoryContext | None:
    if payload is None:
        return None
    return ProjectMemoryContext(
        summary=payload.get("summary", ""),
        known_commands=list(payload.get("known_commands", [])),
        recent_context=payload.get("recent_context", ""),
    )


class AxiomRunManager:
    def __init__(self, state_root: Path | None = None) -> None:
        self.sessions: dict[str, dict] = {}
        self.lock = Lock()
        self.registry_root = state_root or (
            Path(__file__).resolve().parent / ".axiom" / "state"
        )
        self.registry_root.mkdir(parents=True, exist_ok=True)
        self.registry_path = self.registry_root / "session_registry.json"
        self.llm_settings_manager = LLMSettingsManager(self.registry_root)
        self.project_manager = ProjectManager(self.registry_root)
        settings = self.llm_settings_manager.get_settings()
        self.project_manager.update_global_memory(
            model_preference={
                "provider": settings.provider,
                "model": settings.model,
                "enabled": settings.enabled,
                "review_enabled": settings.review_enabled,
            }
        )
        self._load_persisted_sessions()

    def list_projects(self) -> dict:
        context = self.project_manager.get_context()
        return {
            "projects": self.project_manager.list_projects(),
            "active_project_id": context.get("active_project_id"),
            "active_session_id": context.get("active_session_id"),
        }

    def create_project(self, root_path: str, name: str | None = None) -> dict:
        return self.project_manager.create_project(root_path, name=name)

    def get_project(self, project_id: str) -> dict:
        detail = self.project_manager.get_project_detail(project_id)
        with self.lock:
            detail["sessions"] = [
                self._public_session(session)
                for session in sorted(
                    self.sessions.values(),
                    key=lambda item: item.get("updated_at", ""),
                    reverse=True,
                )
                if session.get("project_id") == project_id
            ]
        return detail

    def set_active_project(self, project_id: str) -> dict:
        self.project_manager.get_project(project_id)
        self.project_manager.set_active(project_id, None)
        return self.list_projects()

    def get_llm_settings(self) -> dict:
        return self.llm_settings_manager.get_settings().to_dict()

    def update_llm_settings(self, payload: dict) -> dict:
        settings = self.llm_settings_manager.update_settings(payload)
        self.project_manager.update_global_memory(
            model_preference={
                "provider": settings.provider,
                "model": settings.model,
                "enabled": settings.enabled,
                "review_enabled": settings.review_enabled,
            }
        )
        return settings.to_dict()

    def _public_session(self, session: dict) -> dict:
        public = {}
        for key, value in session.items():
            if key.startswith("_"):
                continue
            public[key] = value
        return json.loads(json.dumps(public))

    def _touch(self, session: dict) -> None:
        session["updated_at"] = _utc_now()

    def _session_file_path(self, project_root: str, session_id: str) -> Path:
        sessions_root = Path(project_root).resolve() / ".axiom" / "sessions"
        sessions_root.mkdir(parents=True, exist_ok=True)
        return sessions_root / f"{session_id}.json"

    def _atomic_write_json(self, path: Path, payload: dict) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f"{path.name}.{uuid4().hex}.tmp")
        temp_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        for attempt in range(5):
            try:
                temp_path.replace(path)
                return
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05)

    def _load_registry(self) -> dict:
        if not self.registry_path.exists():
            return {}
        return json.loads(self.registry_path.read_text(encoding="utf-8"))

    def _registry_entry_for(self, session: dict) -> dict:
        return {
            "id": session["id"],
            "session_id": session.get("session_id", session["id"]),
            "run_id": session.get("run_id", session.get("session_id", session["id"])),
            "project_id": session.get("project_id"),
            "project_root": session["project_root"],
            "path": str(session["_session_file"]),
            "status": session["status"],
            "updated_at": session.get("updated_at"),
            "created_at": session.get("created_at"),
            "artifact_run_id": session.get("artifact_run_id"),
            "mode": session.get("mode"),
        }

    def _update_registry(self, session: dict, session_file: Path) -> None:
        registry = self._load_registry()
        registry[session["id"]] = {
            "id": session["id"],
            "session_id": session.get("session_id", session["id"]),
            "run_id": session.get("run_id", session.get("session_id", session["id"])),
            "project_id": session.get("project_id"),
            "project_root": session["project_root"],
            "path": str(session_file),
            "status": session["status"],
            "updated_at": session.get("updated_at"),
            "created_at": session.get("created_at"),
            "artifact_run_id": session.get("artifact_run_id"),
            "mode": session.get("mode"),
        }
        self._atomic_write_json(self.registry_path, registry)

    def _persist_session(self, session: dict) -> None:
        with PERSISTENCE_LOCK:
            self._touch(session)
            public = self._public_session(session)
            session_file = Path(
                session.get("_session_file")
                or self._session_file_path(session["project_root"], session["id"])
            )
            session["_session_file"] = str(session_file)
            self._atomic_write_json(session_file, public)
            self._update_registry(session, session_file)

    def _store_new_session(self, session: dict) -> None:
        with self.lock:
            self.sessions[session["id"]] = session
        self._persist_session(session)

    def _session(self, session_id: str) -> dict:
        with self.lock:
            return self.sessions[session_id]

    def get_run_state(self, session_id: str) -> dict:
        session = self._session(session_id)
        self.project_manager.set_active(
            session.get("project_id"), session.get("session_id", session["id"])
        )
        return self._public_session(session)

    def list_runs(self) -> list[dict]:
        with self.lock:
            sessions = [
                self._public_session(session) for session in self.sessions.values()
            ]
        return sorted(
            sessions, key=lambda item: item.get("updated_at", ""), reverse=True
        )

    def _load_persisted_sessions(self) -> None:
        registry = self._load_registry()
        loaded: dict[str, dict] = {}
        registry_changed = False
        for session_id, entry in registry.items():
            path = Path(entry["path"])
            if not path.exists():
                registry_changed = True
                continue
            payload = json.loads(path.read_text(encoding="utf-8"))
            session = dict(payload)
            session.setdefault("session_id", session.get("id", session_id))
            session.setdefault(
                "run_id", session.get("session_id", session.get("id", session_id))
            )
            session["_cancellation_event"] = Event()
            session["_thread"] = None
            session["_session_file"] = str(path)
            self._reconcile_loaded_session(session)
            loaded[session_id] = session
        project_changed = self.project_manager.migrate_session_roots(
            list(loaded.values())
        )
        with self.lock:
            self.sessions = loaded
        if project_changed:
            for session in loaded.values():
                self._persist_session(session)
        elif registry_changed:
            cleaned = {
                session_id: self._registry_entry_for(session)
                for session_id, session in loaded.items()
            }
            with PERSISTENCE_LOCK:
                self._atomic_write_json(self.registry_path, cleaned)

    def _reconcile_loaded_session(self, session: dict) -> None:
        if session["status"] not in ACTIVE_RUN_STATUSES:
            return
        prior_status = session["status"]
        self._mark_remaining_steps_skipped(
            session,
            "Skipped because Axiom restarted while this run was active and execution could not be resumed safely.",
        )
        session["cancellation_requested"] = False
        session["pending_phase"] = None
        session["status"] = RunStatus.RECOVERED_AFTER_RESTART.value
        session["final_execution_result"] = {
            "success": False,
            "message": "Run was recovered after restart from an active state. Execution did not resume automatically.",
            "details": {
                "prior_status": prior_status,
                "recovery_reason": "restart_detected_while_active",
                "cancel_reason": session.get("cancel_reason"),
            },
        }
        session["activity"] = list(session.get("activity", []))
        session["activity"].append(
            f"Recovered after restart. Prior active status was '{prior_status}', so the run was terminalized conservatively."
        )
        self._persist_session(session)

    def prepare_run(self, payload: dict) -> dict:
        project_id = payload.get("projectId")
        if project_id:
            project = self.project_manager.get_project_detail(project_id)
        elif payload.get("projectPath"):
            ensured_project = self.project_manager.ensure_project_for_root(
                payload["projectPath"]
            )
            project_id = ensured_project["id"]
            project = self.project_manager.get_project_detail(project_id)
        else:
            raise ValueError("A project must be selected before creating a session.")
        project_root = project["root_path"]
        mode = Mode(payload["mode"])
        task = payload["task"]
        scope_paths = payload.get("scopePaths", [])
        protected_paths = payload.get("protectedPaths", [])
        build_repo_index = bool(payload.get("buildRepoIndex", False))
        preview_changes = bool(payload.get("previewChanges", False))
        auto_repair = bool(payload.get("autoRepair", False))
        approval_mode = ApprovalMode(payload.get("approvalMode", "normal"))
        command_policy = CommandPolicyMode(payload.get("commandPolicy", "permissive"))
        llm_settings = self.llm_settings_manager.get_settings()
        verification = VerificationConfig(
            profile=VerificationProfile(payload.get("verificationProfile", "none")),
            commands=list(payload.get("verificationCommands", [])),
        )
        project_memory = project_memory_from_dict(
            {
                "summary": project.get("memory", {}).get("project_summary", ""),
                "known_commands": project.get("memory", {}).get("known_commands", []),
                "recent_context": project.get("memory", {}).get(
                    "recent_context_summary", ""
                ),
            }
        )
        orchestrator = Orchestrator(project_root, llm_settings=llm_settings)
        self.project_manager.set_active(project_id, None)

        if mode != Mode.IMPLEMENT:
            result = orchestrator.run(
                mode=mode,
                task=task,
                scope_paths=scope_paths,
                protected_paths=protected_paths,
                verification_config=verification,
                project_memory=project_memory,
                build_repo_index=build_repo_index,
                command_policy=command_policy,
                preview_changes=preview_changes,
                auto_repair=auto_repair,
                approval_mode=approval_mode,
            )
            session_id = uuid4().hex
            run_id = uuid4().hex
            now = _utc_now()
            session = {
                "id": session_id,
                "session_id": session_id,
                "run_id": run_id,
                "project_id": project_id,
                "project_root": project_root,
                "project_name": project["name"],
                "status": RunStatus.COMPLETED.value,
                "mode": mode.value,
                "request": payload,
                "result": result.to_dict(),
                "pending_phase": None,
                "activity": [f"{mode.value.title()} run completed."],
                "artifact_run_id": (
                    result.artifact_references[0].label
                    if result.artifact_references
                    else None
                ),
                "created_at": now,
                "updated_at": now,
                "llm_settings": llm_settings.to_dict(),
                "project_memory": project_memory.to_dict() if project_memory else None,
                "session_version": 2,
                "_cancellation_event": Event(),
                "_thread": None,
                "_session_file": str(self._session_file_path(project_root, session_id)),
            }
            self._store_new_session(session)
            self.project_manager.set_active(project_id, session_id)
            return self.get_run_state(session_id)

        permissions = PermissionManager.for_mode(mode)
        artifact_manager = ArtifactManager(project_root)
        artifact_references: list[ArtifactReference] = [
            artifact_manager.run_reference()
        ]
        scope_manager = ScopeManager(project_root, scope_paths, protected_paths)
        workspace = WorkspaceManager(project_root, permissions, scope_manager)
        preview_manager = PreviewManager(workspace, scope_manager, artifact_manager)
        snapshots = SnapshotManager(project_root)
        interpretation = orchestrator._interpret_task(task)

        repo_index_summary = orchestrator._build_repo_index(
            build_repo_index,
            artifact_manager,
            artifact_references,
            workspace,
            scope_manager,
        )

        # Perform Local RAG BEFORE decomposition
        if (
            repo_index_summary
            and llm_settings.enabled
            and getattr(llm_settings, "embedding_model", None)
        ):
            try:
                vector_store = LocalVectorStore(project_root, llm_settings)
                semantic_results = vector_store.search(task, top_k=3)
                if semantic_results:
                    repo_index_summary.semantic_search_results = semantic_results
            except Exception as e:
                print(f"[Axiom] Semantic search failed: {e}")

        if interpretation.action == TaskAction.COMPLEX:
            if not llm_settings.enabled:
                interpretation.action = TaskAction.UNKNOWN
            else:
                decompose_service = LocalLLMDecomposeService(settings=llm_settings)
                subtasks, llm_summary = decompose_service.generate_decomposition(
                    artifact_manager=artifact_manager,
                    interpretation=interpretation,
                    scope_manager=scope_manager,
                    verification=verification,
                    repo_index_summary=repo_index_summary,
                    project_memory=project_memory,
                )
                if subtasks is not None:
                    interpretation.subtasks = subtasks
                else:
                    interpretation.action = TaskAction.UNKNOWN
                if llm_summary is not None:
                    for artifact in llm_summary.artifact_references:
                        if not any(
                            existing.path == artifact.path
                            for existing in artifact_references
                        ):
                            artifact_references.append(artifact)

        change_preview = None
        preview_snapshot_reference = None
        if preview_changes:
            preview_snapshot_reference = snapshots.create_snapshot()
            change_preview = preview_manager.build_preview(
                interpretation,
                snapshot_reference=preview_snapshot_reference,
                plan_steps=[],
                persist=False,
            )
        plan, llm_summary, llm_review_summary = None, None, None
        if interpretation.action != TaskAction.COMPLEX:
            plan, llm_summary, llm_review_summary = (
                orchestrator.build_plan_with_local_llm(
                    mode=mode,
                    interpretation=interpretation,
                    scope_manager=scope_manager,
                    verification=verification,
                    repo_index_summary=repo_index_summary,
                    project_memory=project_memory,
                    artifact_manager=artifact_manager,
                    artifact_references=artifact_references,
                )
            )
        if plan is not None:
            artifact_references.append(
                artifact_manager.save_json(
                    artifact_manager.build_filename("plan"),
                    plan.to_dict(),
                )
            )
        if change_preview is not None and plan is not None:
            change_preview.relevant_steps = [step.title for step in plan.steps]
            change_preview = preview_manager.persist_preview(change_preview)
            if change_preview.artifact_reference is not None:
                artifact_references.append(change_preview.artifact_reference)
        phase_order: list[str] = []
        if plan is not None:
            for step in plan.steps:
                if step.phase not in phase_order:
                    phase_order.append(step.phase)
        phase_policies = classify_plan_phases(plan, interpretation)
        context_package = orchestrator.context_builder.build(
            task=task,
            mode=mode,
            approval_mode=approval_mode,
            effective_scope=scope_manager.describe_effective_scope(),
            protected_paths=scope_manager.protected_path_labels(),
            verification=verification,
            command_policy=command_policy,
            project_memory=project_memory,
            repo_index_summary=repo_index_summary,
            plan=plan,
            phase_policies=phase_policies,
            change_preview=change_preview,
            llm_summary=llm_summary,
            llm_review_summary=llm_review_summary,
            phase_results=[],
            verification_outcomes=[],
            artifact_references=artifact_references,
        )
        session_id = uuid4().hex
        run_id = uuid4().hex
        now = _utc_now()
        session = {
            "id": session_id,
            "session_id": session_id,
            "run_id": run_id,
            "project_id": project_id,
            "project_root": project_root,
            "project_name": project["name"],
            "status": (
                RunStatus.AWAITING_DECOMPOSITION_APPROVAL.value
                if interpretation.action == TaskAction.COMPLEX
                else RunStatus.AWAITING_PLAN_APPROVAL.value
            ),
            "mode": mode.value,
            "request": payload,
            "approval_mode": approval_mode.value,
            "command_policy": command_policy.value,
            "verification": verification.to_dict(),
            "permissions": asdict(permissions),
            "effective_scope": scope_manager.describe_effective_scope(),
            "protected_paths": scope_manager.protected_path_labels(),
            "task_interpretation": interpretation.to_dict(),
            "repo_index_summary": (
                repo_index_summary.to_dict() if repo_index_summary else None
            ),
            "project_memory": project_memory.to_dict() if project_memory else None,
            "change_preview": change_preview.to_dict() if change_preview else None,
            "llm_summary": llm_summary.to_dict() if llm_summary else None,
            "llm_review_summary": (
                llm_review_summary.to_dict() if llm_review_summary else None
            ),
            "plan": plan.to_dict() if plan else None,
            "phase_policies": [policy.to_dict() for policy in phase_policies],
            "context_package": context_package.to_dict(),
            "artifact_references": [
                artifact.to_dict() for artifact in artifact_references
            ],
            "phase_order": phase_order,
            "next_phase_index": 0,
            "pending_phase": None,
            "snapshot_reference": None,
            "preview_snapshot_reference": (
                preview_snapshot_reference.to_dict()
                if preview_snapshot_reference
                else None
            ),
            "step_results": [],
            "subtasks": (
                [st.to_dict() for st in interpretation.subtasks]
                if interpretation.subtasks
                else []
            ),
            "current_subtask_index": 0,
            "phase_results": [],
            "files_read": [],
            "files_modified": [],
            "blocked_actions": [],
            "commands_run": [],
            "initial_execution_result": None,
            "failure_classification": None,
            "repair_summary": None,
            "final_execution_result": None,
            "result": None,
            "last_command_result": None,
            "last_restore_reference": None,
            "activity": ["Prepared IMPLEMENT run and waiting for plan approval."],
            "artifact_run_id": artifact_manager.run_id,
            "cancellation_requested": False,
            "cancel_reason": None,
            "created_at": now,
            "updated_at": now,
            "llm_settings": llm_settings.to_dict(),
            "session_version": 2,
            "_cancellation_event": Event(),
            "_thread": None,
            "_session_file": str(self._session_file_path(project_root, session_id)),
        }
        self._store_new_session(session)
        self.project_manager.set_active(project_id, session_id)
        return self.get_run_state(session_id)

    def approve_plan(self, session_id: str) -> dict:
        session = self._session(session_id)
        if session["status"] != RunStatus.AWAITING_PLAN_APPROVAL.value:
            raise RuntimeError("Plan approval is not pending for this session.")
        snapshot_reference = SnapshotManager(session["project_root"]).create_snapshot()
        session["snapshot_reference"] = snapshot_reference.to_dict()
        session["activity"].append("Plan approved. Workspace snapshot created.")
        self._persist_session(session)
        self._start_worker(session_id)
        return self.get_run_state(session_id)

    def approve_decomposition(self, session_id: str) -> dict:
        session = self._session(session_id)
        if session["status"] != RunStatus.AWAITING_DECOMPOSITION_APPROVAL.value:
            raise RuntimeError(
                "Decomposition approval is not pending for this session."
            )
        session["status"] = RunStatus.RUNNING.value
        session["activity"].append("Decomposition approved.")
        snapshot_reference = SnapshotManager(session["project_root"]).create_snapshot()
        session["snapshot_reference"] = snapshot_reference.to_dict()
        session["activity"].append("Workspace snapshot created.")
        self._persist_session(session)
        self._start_worker(session_id)
        return self.get_run_state(session_id)

    def approve_implementation(self, session_id: str) -> dict:
        session = self._session(session_id)
        if session["status"] != RunStatus.AWAITING_IMPLEMENTATION_APPROVAL.value:
            raise RuntimeError(
                "Implementation approval is not pending for this session."
            )
        session["status"] = RunStatus.RUNNING.value
        session["activity"].append(
            f"Implementation for subtask {session['current_subtask_index']} approved."
        )
        self._persist_session(session)
        self._start_worker(session_id)
        return self.get_run_state(session_id)

    def approve_phase(self, session_id: str) -> dict:
        session = self._session(session_id)
        if (
            session["status"] != RunStatus.AWAITING_PHASE_APPROVAL.value
            or session["pending_phase"] is None
        ):
            raise RuntimeError("No phase approval is pending for this session.")
        session["approved_phase"] = session["pending_phase"]
        session["activity"].append(f"Approved phase '{session['pending_phase']}'.")
        self._persist_session(session)
        self._start_worker(session_id)
        return self.get_run_state(session_id)

    def decline_plan(self, session_id: str, reason: str | None = None) -> dict:
        session = self._session(session_id)
        if session["status"] != RunStatus.AWAITING_PLAN_APPROVAL.value:
            raise RuntimeError("Plan decline is not pending for this session.")
        self._apply_terminal_status(
            session,
            RunStatus.DECLINED_PLAN,
            "Plan was declined. No execution was performed.",
            reason,
        )
        return self.get_run_state(session_id)

    def decline_decomposition(self, session_id: str, reason: str | None = None) -> dict:
        session = self._session(session_id)
        if session["status"] != RunStatus.AWAITING_DECOMPOSITION_APPROVAL.value:
            raise RuntimeError("Decomposition decline is not pending for this session.")
        self._apply_terminal_status(
            session,
            RunStatus.DECLINED_DECOMPOSITION,
            "Decomposition was declined. No execution was performed.",
            reason,
        )
        return self.get_run_state(session_id)

    def decline_implementation(
        self, session_id: str, reason: str | None = None
    ) -> dict:
        session = self._session(session_id)
        if session["status"] != RunStatus.AWAITING_IMPLEMENTATION_APPROVAL.value:
            raise RuntimeError(
                "Implementation decline is not pending for this session."
            )
        self._apply_terminal_status(
            session,
            RunStatus.DECLINED_IMPLEMENTATION,
            f"Implementation for subtask {session['current_subtask_index']} was declined.",
            reason,
        )
        return self.get_run_state(session_id)

    def decline_phase(self, session_id: str, reason: str | None = None) -> dict:
        session = self._session(session_id)
        if (
            session["status"] != RunStatus.AWAITING_PHASE_APPROVAL.value
            or session["pending_phase"] is None
        ):
            raise RuntimeError("No phase approval is pending for this session.")
        self._mark_remaining_steps_skipped(
            session, f"Skipped because phase '{session['pending_phase']}' was declined."
        )
        self._apply_terminal_status(
            session,
            RunStatus.DECLINED_PHASE,
            f"Phase '{session['pending_phase']}' was declined. No further execution was performed.",
            reason,
        )
        return self.get_run_state(session_id)

    def cancel_run(self, session_id: str, reason: str | None = None) -> dict:
        session = self._session(session_id)
        if session["status"] in TERMINAL_RUN_STATUSES:
            return self.get_run_state(session_id)
        session["cancellation_requested"] = True
        session["cancel_reason"] = reason
        session["_cancellation_event"].set()
        if session["status"] in {
            RunStatus.AWAITING_PLAN_APPROVAL.value,
            RunStatus.AWAITING_PHASE_APPROVAL.value,
            RunStatus.AWAITING_DECOMPOSITION_APPROVAL.value,
            RunStatus.AWAITING_IMPLEMENTATION_APPROVAL.value,
        }:
            if session["status"] == RunStatus.AWAITING_PHASE_APPROVAL.value:
                self._mark_remaining_steps_skipped(
                    session,
                    "Skipped because the run was cancelled before the next approval boundary.",
                )
            self._apply_terminal_status(
                session,
                RunStatus.CANCELLED,
                "Run was cancelled. No further execution will occur.",
                reason,
            )
        else:
            session["status"] = RunStatus.CANCELLING.value
            session["activity"].append(
                "Cancellation requested. Execution will stop at the next safe boundary."
            )
            self._persist_session(session)
        return self.get_run_state(session_id)

    def archive_run(self, session_id: str) -> dict:
        session = self._session(session_id)
        session["archived"] = True
        self._persist_session(session)
        return self.get_run_state(session_id)

    def unarchive_run(self, session_id: str) -> dict:
        session = self._session(session_id)
        session["archived"] = False
        self._persist_session(session)
        return self.get_run_state(session_id)

    def queue_task(self, session_id: str, task: str) -> dict:
        session = self._session(session_id)
        if "queued_tasks" not in session:
            session["queued_tasks"] = []
        session["queued_tasks"].append(task)
        session["activity"].append(f"Queued new task: {task}")
        self._persist_session(session)
        return self.get_run_state(session_id)

    def steer_run(self, session_id: str, prompt: str) -> dict:
        session = self._session(session_id)
        if "steering_prompts" not in session:
            session["steering_prompts"] = []
        session["steering_prompts"].append(prompt)
        session["activity"].append(f"Received steering input: {prompt}")
        self._persist_session(session)
        return self.get_run_state(session_id)

    def _start_worker(self, session_id: str) -> None:
        session = self._session(session_id)
        existing = session.get("_thread")
        if existing is not None and existing.is_alive():
            return
        worker = Thread(target=self._run_session, args=(session_id,), daemon=True)
        session["_thread"] = worker
        worker.start()

    def _run_session(self, session_id: str) -> None:
        session = self._session(session_id)
        try:
            while True:
                if self._check_cancelled(session):
                    return

                interpretation = interpretation_from_dict(
                    session["task_interpretation"]
                )
                if interpretation.action == TaskAction.COMPLEX.value:
                    if session.get("completed_subtask_indices") is None:
                        session["completed_subtask_indices"] = []

                    # Find next unblocked subtask based on dependencies
                    if session.get("current_subtask_index", -1) in session.get(
                        "completed_subtask_indices", []
                    ):
                        session["current_subtask_index"] = -1

                    if session.get("current_subtask_index", -1) == -1:
                        next_index = -1
                        for idx, st in enumerate(interpretation.subtasks or []):
                            if idx in session["completed_subtask_indices"]:
                                continue

                            # Check if dependencies are met
                            deps_met = True
                            if st.dependencies:
                                for dep in st.dependencies:
                                    # Simple exact string match against completed descriptions
                                    # or we check if ALL dependencies are met. A more robust ID system
                                    # would be better, but we do best effort matching against descriptions.
                                    dep_found_and_done = False
                                    for done_idx in session[
                                        "completed_subtask_indices"
                                    ]:
                                        if interpretation.subtasks and done_idx < len(
                                            interpretation.subtasks
                                        ):
                                            done_desc = interpretation.subtasks[
                                                done_idx
                                            ].description
                                            if (
                                                dep.lower() in done_desc.lower()
                                                or done_desc.lower() in dep.lower()
                                                or dep == str(done_idx)
                                            ):
                                                dep_found_and_done = True
                                                break
                                    if not dep_found_and_done:
                                        deps_met = False
                                        break

                            if deps_met:
                                next_index = idx
                                break

                        session["current_subtask_index"] = next_index
                        self._persist_session(session)

                    if session["current_subtask_index"] == -1 or session[
                        "current_subtask_index"
                    ] >= len(interpretation.subtasks or []):
                        # No more available subtasks. Either we're done, or blocked by dependencies.
                        if len(session.get("completed_subtask_indices", [])) < len(
                            interpretation.subtasks or []
                        ):
                            self._apply_terminal_status(
                                session,
                                RunStatus.FAILED,
                                "Deadlock: remaining subtasks have unsatisfied dependencies.",
                                None,
                            )
                            return

                        llm_settings = LLMSettings.from_dict(
                            session.get("llm_settings")
                            or self.llm_settings_manager.get_settings().to_dict()
                        )
                        project_root = session["project_root"]
                        artifact_manager = ArtifactManager(
                            project_root, run_id=session["artifact_run_id"]
                        )

                        # Compress history if enabled and threshold exceeded
                        if (
                            llm_settings.compression_enabled
                            and interpretation.subtasks is not None
                        ):
                            uncompressed_count = (
                                session["current_subtask_index"]
                                - interpretation.compressed_subtask_count
                            )
                            if uncompressed_count >= llm_settings.compression_threshold:
                                compress_service = LocalLLMCompressService(
                                    settings=llm_settings
                                )
                                subtasks_to_compress = interpretation.subtasks[
                                    interpretation.compressed_subtask_count : session[
                                        "current_subtask_index"
                                    ]
                                ]

                                compressed_text, compress_summary = (
                                    compress_service.generate_compression(
                                        artifact_manager=artifact_manager,
                                        interpretation=interpretation,
                                        subtasks_to_compress=subtasks_to_compress,
                                        existing_compressed_history=interpretation.compressed_history,
                                    )
                                )

                                if compressed_text:
                                    interpretation.compressed_history = compressed_text
                                    interpretation.compressed_subtask_count = session[
                                        "current_subtask_index"
                                    ]
                                    session["task_interpretation"] = (
                                        interpretation.to_dict()
                                    )
                                    session["activity"].append(
                                        f"Compressed {len(subtasks_to_compress)} subtasks into history."
                                    )
                                    self._persist_session(session)

                        # Try to replan
                        replan_service = LocalLLMReplanService(settings=llm_settings)
                        scope_manager = ScopeManager(
                            project_root,
                            session["request"].get("scopePaths", []),
                            session["request"].get("protectedPaths", []),
                        )
                        verification = verification_from_dict(session["verification"])
                        repo_index_summary = repo_index_summary_from_dict(
                            session["repo_index_summary"]
                        )
                        project_memory = project_memory_from_dict(
                            session.get("project_memory")
                        )

                        replan_payload, llm_summary = replan_service.generate_replan(
                            artifact_manager=artifact_manager,
                            interpretation=interpretation,
                            completed_subtasks=interpretation.subtasks or [],
                            scope_manager=scope_manager,
                            verification=verification,
                            repo_index_summary=repo_index_summary,
                            project_memory=project_memory,
                        )

                        if llm_summary is not None:
                            for artifact in llm_summary.artifact_references:
                                candidate = artifact.to_dict()
                                if candidate not in session["artifact_references"]:
                                    session["artifact_references"].append(candidate)

                        if replan_payload is None or replan_payload.get(
                            "is_complete", True
                        ):
                            self._finalize_session(session)
                            return

                        # If more work is needed, append subtasks and require decomposition approval again
                        new_subtasks = [
                            SubTask(
                                action=st["action"],
                                description=st["description"],
                                target_path=st.get("target_path"),
                                command=st.get("command"),
                                dependencies=st.get("dependencies", []),
                            )
                            for st in replan_payload.get("new_subtasks", [])
                        ]

                        if not new_subtasks:
                            # Failsafe if it says not complete but gives no tasks
                            self._finalize_session(session)
                            return

                        if interpretation.subtasks is None:
                            interpretation.subtasks = []
                        interpretation.subtasks.extend(new_subtasks)
                        session["task_interpretation"] = interpretation.to_dict()
                        session["subtasks"] = [
                            st.to_dict() for st in interpretation.subtasks
                        ]
                        session["status"] = (
                            RunStatus.AWAITING_DECOMPOSITION_APPROVAL.value
                        )
                        session["activity"].append(
                            "Replanning identified additional subtasks needed. Awaiting decomposition approval."
                        )
                        self._persist_session(session)
                        return

                    subtasks_list = interpretation.subtasks or []
                    subtask = subtasks_list[session["current_subtask_index"]]
                    if (
                        session.get("current_subtask_content") is None
                        and session.get("current_subtask_command") is None
                        and subtask.action
                        in {"create_file", "modify_file", "run_command"}
                    ):
                        # We need to implement this subtask
                        self._generate_and_await_implementation(session, subtask)
                        return
                    else:
                        self._execute_subtask(session, subtask)

                        # If subtask paused for approval, wait for human input
                        if (
                            session["status"] not in {RunStatus.RUNNING.value}
                            and session["status"] not in TERMINAL_RUN_STATUSES
                        ):
                            return

                        if session["status"] in TERMINAL_RUN_STATUSES:
                            # Rather than strictly returning, if a subtask fails, we could potentially trigger replan here.
                            # For now, we follow the simple path of failing if a subtask fails terminally.
                            return

                        # Update the main task interpretation in session to capture the subtask's result_summary
                        session["task_interpretation"] = interpretation.to_dict()

                        llm_settings = LLMSettings.from_dict(
                            session.get("llm_settings")
                            or self.llm_settings_manager.get_settings().to_dict()
                        )

                        if llm_settings.enabled and llm_settings.review_enabled:
                            from llm_eval_service import LocalLLMEvalService

                            eval_service = LocalLLMEvalService(settings=llm_settings)
                            project_root = session["project_root"]
                            artifact_manager = ArtifactManager(
                                project_root, run_id=session["artifact_run_id"]
                            )
                            scope_manager = ScopeManager(
                                project_root,
                                session["request"].get("scopePaths", []),
                                session["request"].get("protectedPaths", []),
                            )
                            verification = verification_from_dict(session["verification"])
                            repo_index_summary = repo_index_summary_from_dict(
                                session["repo_index_summary"]
                            )
                            project_memory = project_memory_from_dict(
                                session.get("project_memory")
                            )

                            eval_payload, eval_summary = eval_service.generate_eval(
                                artifact_manager=artifact_manager,
                                interpretation=interpretation,
                                subtask=subtask,
                                scope_manager=scope_manager,
                                verification=verification,
                                repo_index_summary=repo_index_summary,
                                project_memory=project_memory,
                            )

                            if eval_summary is not None:
                                for artifact in eval_summary.artifact_references:
                                    candidate = artifact.to_dict()
                                    if candidate not in session["artifact_references"]:
                                        session["artifact_references"].append(candidate)

                            # If evaluation fails, we append a new "fix" subtask immediately to address it
                            if eval_payload is not None and not eval_payload.get("success", True):
                                session["activity"].append(
                                    f"Evaluation found subtask failed: {eval_payload.get('reasoning')} - Injecting a follow up task."
                                )
                                fix_subtask = SubTask(
                                    action="complex",
                                    description=f"Fix the previous subtask which failed: {eval_payload.get('reasoning')}",
                                    dependencies=[subtask.description]
                                )
                                if interpretation.subtasks is None:
                                    interpretation.subtasks = []
                                interpretation.subtasks.append(fix_subtask)
                                session["task_interpretation"] = interpretation.to_dict()
                                session["subtasks"] = [st.to_dict() for st in interpretation.subtasks]

                        session["completed_subtask_indices"].append(
                            session["current_subtask_index"]
                        )
                        session["current_subtask_index"] = -1
                        session["current_subtask_content"] = None
                        session["current_subtask_command"] = None
                        continue

                if session["next_phase_index"] >= len(session["phase_order"]):
                    self._finalize_session(session)
                    return
                phase = session["phase_order"][session["next_phase_index"]]
                policy = next(
                    (
                        item
                        for item in session.get("phase_policies", [])
                        if item["phase"] == phase
                    ),
                    None,
                )
                if (
                    session["approval_mode"] == ApprovalMode.PHASED.value
                    and policy is not None
                    and policy["approval_required"]
                ):
                    if session.get("approved_phase") != phase:
                        session["pending_phase"] = phase
                        session["status"] = RunStatus.AWAITING_PHASE_APPROVAL.value
                        session["activity"].append(
                            f"Waiting for approval of phase '{phase}'."
                        )
                        self._persist_session(session)
                        return
                    session["approved_phase"] = None
                if policy is not None and policy["auto_run_allowed"]:
                    policy["auto_ran"] = True
                    session["status"] = RunStatus.AUTO_RUNNING_READ_ONLY_PHASE.value
                    session["activity"].append(
                        f"Auto-ran phase '{phase}' because it is system-classified as {policy['classification']}."
                    )
                else:
                    session["status"] = RunStatus.RUNNING.value
                self._persist_session(session)
                self._execute_phase(session, phase)
                if session["status"] in TERMINAL_RUN_STATUSES:
                    return
                session["next_phase_index"] += 1
                self._persist_session(session)
        except Exception as error:
            import traceback

            session["final_execution_result"] = ExecutionResult(
                success=False,
                message=str(error),
                details={"traceback": traceback.format_exc()},
            ).to_dict()
            session["status"] = RunStatus.FAILED.value
            session["activity"].append(f"Run failed unexpectedly: {error}")
            self._persist_session(session)

    def _check_cancelled(self, session: dict) -> bool:
        if not session.get("cancellation_requested"):
            return False
        self._mark_remaining_steps_skipped(
            session,
            "Skipped because cancellation was requested and execution stopped at a safe boundary.",
        )
        self._apply_terminal_status(
            session,
            RunStatus.CANCELLED,
            "Run was cancelled. No further execution will occur.",
            session.get("cancel_reason"),
        )
        return True

    def _generate_and_await_implementation(self, session: dict, subtask) -> None:
        project_root = session["project_root"]
        llm_settings = LLMSettings.from_dict(
            session.get("llm_settings")
            or self.llm_settings_manager.get_settings().to_dict()
        )
        artifact_manager = ArtifactManager(
            project_root, run_id=session["artifact_run_id"]
        )
        scope_manager = ScopeManager(
            project_root,
            session["request"].get("scopePaths", []),
            session["request"].get("protectedPaths", []),
        )
        repo_index_summary = repo_index_summary_from_dict(session["repo_index_summary"])
        project_memory = project_memory_from_dict(session.get("project_memory"))

        existing_content = None
        if subtask.action in {"modify_file"} and subtask.target_path:
            workspace = WorkspaceManager(
                project_root, PermissionSet(**session["permissions"]), scope_manager
            )
            target = workspace.resolve_path(subtask.target_path)
            if target.exists():
                existing_content = workspace.read_text(subtask.target_path)

        if subtask.action not in {"create_file", "modify_file", "run_command"}:
            session["status"] = RunStatus.RUNNING.value
            self._persist_session(session)
            return

        implement_service = LocalLLMImplementService(settings=llm_settings)
        result_payload, llm_summary = implement_service.generate_implementation(
            artifact_manager=artifact_manager,
            subtask=subtask,
            scope_manager=scope_manager,
            repo_index_summary=repo_index_summary,
            project_memory=project_memory,
            existing_content=existing_content,
        )

        if result_payload is None:
            self._apply_terminal_status(
                session,
                RunStatus.FAILED,
                f"Failed to generate implementation for subtask: {subtask.description}",
                None,
            )
            return

        if subtask.action in {"create_file", "modify_file"}:
            session["current_subtask_content"] = result_payload
        else:
            session["current_subtask_command"] = result_payload

        session["status"] = RunStatus.AWAITING_IMPLEMENTATION_APPROVAL.value
        session["activity"].append(
            f"Generated implementation for subtask {session['current_subtask_index']}. Awaiting approval."
        )

        if llm_summary is not None:
            for artifact in llm_summary.artifact_references:
                candidate = artifact.to_dict()
                if candidate not in session["artifact_references"]:
                    session["artifact_references"].append(candidate)

        self._persist_session(session)

    def _execute_subtask(self, session: dict, subtask) -> None:
        # Construct a task interpretation dynamically from the subtask state
        try:
            action_val = TaskAction(subtask.action)
        except ValueError:
            action_val = TaskAction.UNKNOWN

        sub_interpretation = TaskInterpretation(
            raw_task=subtask.description,
            summary=subtask.description,
            action=action_val,
            target_path=subtask.target_path,
            command=(
                session.get("current_subtask_command")
                if subtask.action == "run_command"
                else None
            ),
            content=(
                session.get("current_subtask_content")
                if subtask.action in {"create_file", "modify_file"}
                else None
            ),
            snapshot_id=None,
        )

        # Convert into a fake Plan and execution payload to leverage existing logic
        from planner import Planner

        project_root = session["project_root"]
        scope_manager = ScopeManager(
            project_root,
            session["request"].get("scopePaths", []),
            session["request"].get("protectedPaths", []),
        )
        verification = verification_from_dict(session["verification"])
        repo_index_summary = repo_index_summary_from_dict(session["repo_index_summary"])

        plan = Planner().build_plan(
            Mode(session["mode"]),
            sub_interpretation,
            scope_manager,
            verification,
            repo_index_summary,
        )

        if plan is None:
            raise RuntimeError("Could not construct a valid plan for subtask execution")

        session["plan"] = (
            plan.to_dict()
        )  # override the session's plan for this subtask to allow _execute_phase to reuse logic
        phase_order = ["understand", "modify", "verify"]

        # Initialize the subtask phase loop explicitly
        if "subtask_phase_index" not in session or session[
            "subtask_phase_index"
        ] >= len(phase_order):
            session["subtask_phase_index"] = 0

        while session["subtask_phase_index"] < len(phase_order):
            phase = phase_order[session["subtask_phase_index"]]

            # Apply phase policies dynamically if phased approval is enabled
            policy = next(
                (
                    item
                    for item in session.get("phase_policies", [])
                    if item["phase"] == phase
                ),
                None,
            )
            if (
                session["approval_mode"] == ApprovalMode.PHASED.value
                and policy is not None
                and policy["approval_required"]
            ):
                if session.get("approved_phase") != phase:
                    session["pending_phase"] = phase
                    session["status"] = RunStatus.AWAITING_PHASE_APPROVAL.value
                    session["activity"].append(
                        f"Waiting for approval of phase '{phase}'."
                    )
                    self._persist_session(session)
                    return
                session["approved_phase"] = None

            if policy is not None and policy["auto_run_allowed"]:
                policy["auto_ran"] = True
                session["status"] = RunStatus.AUTO_RUNNING_READ_ONLY_PHASE.value
                session["activity"].append(
                    f"Auto-ran phase '{phase}' because it is system-classified as {policy['classification']}."
                )
            else:
                session["status"] = RunStatus.RUNNING.value

            self._persist_session(session)
            self._execute_phase(session, phase)

            if session["status"] in TERMINAL_RUN_STATUSES:
                return

            session["subtask_phase_index"] += 1

        # Once we are done with the inner subtask phase loop, reset the index for the next subtask
        session["subtask_phase_index"] = 0

        # Record result summary
        if session["step_results"]:
            last_step = session["step_results"][-1]
            if "details" in last_step:
                subtask.result_summary = f"Status: {last_step['status']}. Message: {last_step['message']}. Details: {last_step['details']}"
            else:
                subtask.result_summary = (
                    f"Status: {last_step['status']}. Message: {last_step['message']}"
                )

        # Check for steering prompts after subtask execution
        if session.get("steering_prompts"):
            steering_prompt = session["steering_prompts"].pop(0)
            session["activity"].append(f"Reacting to steering: {steering_prompt}")

            # Update the ROOT task interpretation to include the steering prompt
            root_interpretation = interpretation_from_dict(session["task_interpretation"])
            root_interpretation.raw_task = f"{root_interpretation.raw_task}\n\n[USER STEER]: {steering_prompt}"
            session["task_interpretation"] = root_interpretation.to_dict()

            # Force a replan by setting current_subtask_index to -1 and tricking the loop
            session["current_subtask_index"] = -1
            self._persist_session(session)

    def _execute_phase(self, session: dict, phase: str) -> None:
        plan = plan_from_dict(session["plan"])
        if plan is None:
            raise RuntimeError("No plan is available for this session.")
        phase_steps = [step for step in plan.steps if step.phase == phase]
        if not phase_steps:
            return

        project_root = session["project_root"]
        permissions = PermissionSet(**session["permissions"])
        command_policy = CommandPolicyMode(session["command_policy"])
        verification = verification_from_dict(session["verification"])
        interpretation = interpretation_from_dict(session["task_interpretation"])

        # If it's a COMPLEX action, use the dynamic subtask interpretation
        # Otherwise, stick to the root interpretation.
        if (
            interpretation.action == TaskAction.COMPLEX
            and interpretation.subtasks
        ):
            subtask = interpretation.subtasks[session["current_subtask_index"]]

            # Use TaskAction for standard actions, fallback to UNKNOWN for unsupported like 'analyze'
            try:
                action_val = TaskAction(subtask.action)
            except ValueError:
                action_val = TaskAction.UNKNOWN

            interpretation = TaskInterpretation(
                raw_task=subtask.description,
                summary=subtask.description,
                action=action_val,
                target_path=subtask.target_path,
                command=(
                    session.get("current_subtask_command")
                    if subtask.action == "run_command"
                    else None
                ),
                content=(
                    session.get("current_subtask_content")
                    if subtask.action in {"create_file", "modify_file"}
                    else None
                ),
                snapshot_id=None,
            )
        elif interpretation.action == TaskAction.COMPLEX:
            # Fallback to UNKNOWN if decomposition failed or LLM was disabled
            interpretation.action = TaskAction.UNKNOWN

        orchestrator = Orchestrator(project_root)
        artifact_manager = ArtifactManager(
            project_root, run_id=session["artifact_run_id"]
        )
        scope_manager = ScopeManager(
            project_root,
            session["request"].get("scopePaths", []),
            session["request"].get("protectedPaths", []),
        )
        workspace = WorkspaceManager(project_root, permissions, scope_manager)
        terminal = TerminalRunner(
            project_root, permissions, command_policy, artifact_manager
        )
        verification_manager = VerificationManager(verification, workspace, terminal)
        snapshots = SnapshotManager(project_root)
        step_results = [step_result_from_dict(item) for item in session["step_results"]]
        cancellation_event = session["_cancellation_event"]

        def merge_runtime_state() -> None:
            for value in workspace.files_read:
                if value not in session["files_read"]:
                    session["files_read"].append(value)
            for value in workspace.files_modified:
                if value not in session["files_modified"]:
                    session["files_modified"].append(value)
            for action in workspace.blocked_actions:
                candidate = action.to_dict()
                if candidate not in session["blocked_actions"]:
                    session["blocked_actions"].append(candidate)
            for command in terminal.commands_run:
                candidate = command.to_dict()
                if candidate not in session["commands_run"]:
                    session["commands_run"].append(candidate)
                session["last_command_result"] = candidate
                if command.cancelled:
                    session["cancellation_requested"] = True
            for artifact in [artifact_manager.run_reference()] + [
                command.artifact_reference
                for command in terminal.commands_run
                if command.artifact_reference is not None
            ]:
                candidate = artifact.to_dict()
                if candidate not in session["artifact_references"]:
                    session["artifact_references"].append(candidate)

        try:
            self._execute_phase_action(
                session,
                phase,
                plan,
                phase_steps[0],
                interpretation,
                orchestrator,
                workspace,
                scope_manager,
                terminal,
                verification,
                verification_manager,
                snapshots,
                step_results,
                cancellation_event,
            )
        except Exception as error:
            current_step = phase_steps[0]
            completed_ids = {item.step_id for item in step_results}
            if current_step.id not in completed_ids:
                step_results.append(
                    StepResult(
                        step_id=current_step.id,
                        title=current_step.title,
                        step_type=current_step.step_type,
                        status=StepStatus.FAILED,
                        message=str(error),
                        phase=current_step.phase,
                        details={},
                    )
                )
            remaining_steps = [
                candidate
                for candidate in plan.steps
                if candidate.id not in {item.step_id for item in step_results}
            ]
            step_results.extend(orchestrator._skip_remaining_steps(remaining_steps))
            merge_runtime_state()
            session["step_results"] = [item.to_dict() for item in step_results]
            self._persist_session(session)
            if session.get("cancellation_requested"):
                self._check_cancelled(session)
            else:
                session["initial_execution_result"] = ExecutionResult(
                    success=False, message=str(error), details={}
                ).to_dict()
                self._finalize_session(session)
            return

        merge_runtime_state()
        session["step_results"] = [item.to_dict() for item in step_results]
        if session.get("cancellation_requested"):
            self._persist_session(session)
            self._check_cancelled(session)
            return
        phase_results = orchestrator._build_phase_results_with_policy(
            step_results,
            [
                phase_policy_from_dict(item)
                for item in session.get("phase_policies", [])
            ],
        )
        session["phase_results"] = [
            phase_result.to_dict() for phase_result in phase_results
        ]
        session["activity"].append(f"Phase '{phase}' completed.")
        session["pending_phase"] = None
        self._persist_session(session)

    def _execute_phase_action(
        self,
        session: dict,
        phase: str,
        plan: Plan,
        step: PlanStep,
        interpretation: TaskInterpretation,
        orchestrator: Orchestrator,
        workspace: WorkspaceManager,
        scope_manager: ScopeManager,
        terminal: TerminalRunner,
        verification: VerificationConfig,
        verification_manager: VerificationManager,
        snapshots: SnapshotManager,
        step_results: list[StepResult],
        cancellation_event: Event,
    ) -> None:
        if cancellation_event.is_set():
            raise RuntimeError(
                "Run cancellation was requested before the phase started."
            )
        action = interpretation.action
        if action in {TaskAction.CREATE_FILE, TaskAction.MODIFY_FILE}:
            self._execute_file_phase(
                phase,
                step,
                action,
                interpretation,
                orchestrator,
                workspace,
                scope_manager,
                verification,
                verification_manager,
                step_results,
                cancellation_event,
            )
            return
        if action == TaskAction.RUN_COMMAND:
            self._execute_command_phase(
                session,
                phase,
                step,
                interpretation,
                orchestrator,
                terminal,
                verification,
                verification_manager,
                step_results,
                plan,
                cancellation_event,
            )
            return
        if action == TaskAction.RESTORE_SNAPSHOT:
            self._execute_restore_phase(
                session,
                phase,
                step,
                interpretation,
                orchestrator,
                scope_manager,
                snapshots,
                verification,
                verification_manager,
                step_results,
                cancellation_event,
            )
            return
        raise RuntimeError(
            "This session manager supports only the current IMPLEMENT action set."
        )

    def _execute_file_phase(
        self,
        phase: str,
        step: PlanStep,
        action: TaskAction,
        interpretation: TaskInterpretation,
        orchestrator: Orchestrator,
        workspace: WorkspaceManager,
        scope_manager: ScopeManager,
        verification: VerificationConfig,
        verification_manager: VerificationManager,
        step_results: list[StepResult],
        cancellation_event: Event,
    ) -> None:
        assert interpretation.target_path is not None
        assert interpretation.content is not None
        target = workspace.resolve_path(interpretation.target_path)
        if phase == "understand":
            scope_manager.enforce_write(target)
            existed_before = target.exists()
            if action == TaskAction.MODIFY_FILE and existed_before:
                workspace.read_text(interpretation.target_path)
            message = (
                "Existing file state inspected."
                if action == TaskAction.MODIFY_FILE
                else "Target path inspected."
            )
            step_results.append(
                orchestrator._completed_step(
                    step,
                    message,
                    {
                        "target_path": interpretation.target_path,
                        "already_exists": existed_before,
                    },
                )
            )
            return
        if phase == "modify":
            if cancellation_event.is_set():
                raise RuntimeError(
                    "Run cancellation was requested before the file write started."
                )
            scope_manager.enforce_write(target)
            existed_before = target.exists()
            workspace.write_text(interpretation.target_path, interpretation.content)
            message = (
                "Requested file content was overwritten."
                if action == TaskAction.MODIFY_FILE
                else "Requested file content was written."
            )
            step_results.append(
                orchestrator._completed_step(
                    step,
                    message,
                    {
                        "target_path": interpretation.target_path,
                        "created_new_file": not existed_before,
                    },
                )
            )
            return
        if phase == "verify":
            verification_step = orchestrator._run_verification_step(
                step=step,
                action=action,
                target_path=interpretation.target_path,
                expected_content=interpretation.content,
                verification=verification,
                verification_manager=verification_manager,
                cancellation_event=cancellation_event,
            )
            step_results.append(verification_step)
            return
        raise RuntimeError(f"Unsupported phase '{phase}' for file action.")

    def _execute_command_phase(
        self,
        session: dict,
        phase: str,
        step: PlanStep,
        interpretation: TaskInterpretation,
        orchestrator: Orchestrator,
        terminal: TerminalRunner,
        verification: VerificationConfig,
        verification_manager: VerificationManager,
        step_results: list[StepResult],
        plan: Plan,
        cancellation_event: Event,
    ) -> None:
        assert interpretation.command is not None
        if phase == "understand":
            step_results.append(
                orchestrator._completed_step(
                    step,
                    "Command context confirmed.",
                    {
                        "command": interpretation.command,
                        "project_root": str(Path(terminal.project_root).resolve()),
                    },
                )
            )
            return
        if phase == "modify":
            result = terminal.run(
                interpretation.command, cancellation_event=cancellation_event
            )
            step_results.append(
                StepResult(
                    step_id=step.id,
                    title=step.title,
                    step_type=step.step_type,
                    status=(
                        StepStatus.COMPLETED if result.success else StepStatus.FAILED
                    ),
                    message=result.summary,
                    phase=step.phase,
                    details=result.to_dict(),
                )
            )
            if not result.success:
                remaining_steps = [
                    candidate
                    for candidate in plan.steps
                    if candidate.id not in {item.step_id for item in step_results}
                ]
                step_results.extend(orchestrator._skip_remaining_steps(remaining_steps))
                if result.cancelled:
                    session["cancellation_requested"] = True
                else:
                    session["initial_execution_result"] = ExecutionResult(
                        success=False,
                        message="Command execution failed.",
                        details=result.to_dict(),
                    ).to_dict()
            return
        if phase == "verify":
            command_result = (
                command_result_from_dict(session["last_command_result"])
                if session["last_command_result"] is not None
                else None
            )
            verification_step = orchestrator._run_verification_step(
                step=step,
                action=TaskAction.RUN_COMMAND,
                target_path=None,
                expected_content=None,
                verification=verification,
                verification_manager=verification_manager,
                command_result=command_result,
                cancellation_event=cancellation_event,
            )
            step_results.append(verification_step)
            return
        raise RuntimeError(f"Unsupported phase '{phase}' for command action.")

    def _execute_restore_phase(
        self,
        session: dict,
        phase: str,
        step: PlanStep,
        interpretation: TaskInterpretation,
        orchestrator: Orchestrator,
        scope_manager: ScopeManager,
        snapshots: SnapshotManager,
        verification: VerificationConfig,
        verification_manager: VerificationManager,
        step_results: list[StepResult],
        cancellation_event: Event,
    ) -> None:
        if phase == "understand":
            requested = interpretation.snapshot_id or "latest snapshot"
            reference = (
                snapshots.latest_snapshot()
                if interpretation.snapshot_id is None
                else snapshots._reference_for(interpretation.snapshot_id)
            )
            session["last_restore_reference"] = reference.to_dict()
            step_results.append(
                orchestrator._completed_step(
                    step,
                    "Snapshot source identified.",
                    {"requested_snapshot": requested, **reference.to_dict()},
                )
            )
            return
        if phase == "modify":
            if cancellation_event.is_set():
                raise RuntimeError(
                    "Run cancellation was requested before restore began."
                )
            scope_manager.enforce_full_workspace_write("restore")
            restored = snapshots.restore_snapshot(interpretation.snapshot_id)
            session["last_restore_reference"] = restored.to_dict()
            step_results.append(
                orchestrator._completed_step(
                    step, "Workspace restored from snapshot.", restored.to_dict()
                )
            )
            return
        if phase == "verify":
            verification_step = orchestrator._run_verification_step(
                step=step,
                action=TaskAction.RESTORE_SNAPSHOT,
                target_path=None,
                expected_content=None,
                verification=verification,
                verification_manager=verification_manager,
                cancellation_event=cancellation_event,
            )
            step_results.append(verification_step)
            return
        raise RuntimeError(f"Unsupported phase '{phase}' for restore action.")

    def _mark_remaining_steps_skipped(self, session: dict, message: str) -> None:
        plan = plan_from_dict(session["plan"])
        if plan is None:
            return
        completed_ids = {item["step_id"] for item in session["step_results"]}
        for step in plan.steps:
            if step.id in completed_ids:
                continue
            session["step_results"].append(
                StepResult(
                    step_id=step.id,
                    title=step.title,
                    step_type=step.step_type,
                    status=StepStatus.SKIPPED,
                    message=message,
                    phase=step.phase,
                    details={},
                ).to_dict()
            )
        orchestrator = Orchestrator(session["project_root"])
        phase_results = orchestrator._build_phase_results_with_policy(
            [step_result_from_dict(item) for item in session["step_results"]],
            [
                phase_policy_from_dict(item)
                for item in session.get("phase_policies", [])
            ],
        )
        session["phase_results"] = [phase.to_dict() for phase in phase_results]

    def _apply_terminal_status(
        self, session: dict, status: RunStatus, message: str, reason: str | None
    ) -> None:
        session["status"] = status.value
        session["pending_phase"] = None
        session["final_execution_result"] = {
            "success": False,
            "message": message,
            "details": {"reason": reason} if reason else {},
        }
        session["activity"].append(
            message if not reason else f"{message} Reason: {reason}"
        )
        self._persist_session(session)

    def _finalize_session(self, session: dict) -> None:
        project_root = session["project_root"]
        mode = Mode(session["mode"])
        permissions = PermissionSet(**session["permissions"])
        approval_mode = ApprovalMode(session["approval_mode"])
        command_policy = CommandPolicyMode(session["command_policy"])
        verification = verification_from_dict(session["verification"])
        interpretation = interpretation_from_dict(session["task_interpretation"])
        repo_index_summary = repo_index_summary_from_dict(session["repo_index_summary"])
        plan = plan_from_dict(session["plan"])
        change_preview = change_preview_from_dict(session["change_preview"])
        project_memory = project_memory_from_dict(session.get("project_memory"))
        llm_summary = llm_summary_from_dict(session.get("llm_summary"))
        llm_review_summary = llm_summary_from_dict(session.get("llm_review_summary"))
        artifact_references = [
            ArtifactReference(**artifact) for artifact in session["artifact_references"]
        ]
        step_results = [step_result_from_dict(item) for item in session["step_results"]]

        llm_settings = LLMSettings.from_dict(
            session.get("llm_settings")
            or self.llm_settings_manager.get_settings().to_dict()
        )
        orchestrator = Orchestrator(project_root, llm_settings=llm_settings)
        artifact_manager = ArtifactManager(
            project_root, run_id=session["artifact_run_id"]
        )
        scope_manager = ScopeManager(
            project_root,
            session["request"].get("scopePaths", []),
            session["request"].get("protectedPaths", []),
        )
        workspace = WorkspaceManager(project_root, permissions, scope_manager)
        workspace.files_read = list(session["files_read"])
        workspace.files_modified = list(session["files_modified"])
        workspace.blocked_actions = [
            blocked_action_from_dict(item) for item in session["blocked_actions"]
        ]
        terminal = TerminalRunner(
            project_root, permissions, command_policy, artifact_manager
        )
        terminal.commands_run = [
            command_result_from_dict(item) for item in session["commands_run"]
        ]
        verification_manager = VerificationManager(verification, workspace, terminal)

        if session.get("cancellation_requested"):
            self._mark_remaining_steps_skipped(
                session, "Skipped because the run was cancelled."
            )
            self._apply_terminal_status(
                session,
                RunStatus.CANCELLED,
                "Run was cancelled. No further execution will occur.",
                session.get("cancel_reason"),
            )
            return

        if session["initial_execution_result"] is None:
            session["initial_execution_result"] = self._build_success_execution_result(
                session
            ).to_dict()
        initial_execution_result = ExecutionResult(
            **session["initial_execution_result"]
        )
        final_execution_result, failure_classification, repair_summary = (
            orchestrator._handle_post_execution_failure(
                artifact_manager=artifact_manager,
                artifact_references=artifact_references,
                auto_repair=bool(session["request"].get("autoRepair", False)),
                initial_execution_result=initial_execution_result,
                step_results=step_results,
                workspace=workspace,
                terminal=terminal,
                verification_manager=verification_manager,
                interpretation=interpretation,
                verification=verification,
                repo_index_summary=repo_index_summary,
                scope_manager=scope_manager,
            )
        )
        phase_results = orchestrator._build_phase_results_with_policy(
            step_results,
            [
                phase_policy_from_dict(item)
                for item in session.get("phase_policies", [])
            ],
        )
        verification_outcomes = [
            step.details
            for step in step_results
            if step.step_type == StepType.VERIFICATION and step.details
        ]
        context_package = orchestrator.context_builder.build(
            task=session["request"]["task"],
            mode=mode,
            approval_mode=approval_mode,
            effective_scope=scope_manager.describe_effective_scope(),
            protected_paths=scope_manager.protected_path_labels(),
            verification=verification,
            command_policy=command_policy,
            project_memory=project_memory,
            repo_index_summary=repo_index_summary,
            plan=plan,
            phase_policies=[
                phase_policy_from_dict(item)
                for item in session.get("phase_policies", [])
            ],
            change_preview=change_preview,
            llm_summary=llm_summary,
            llm_review_summary=llm_review_summary,
            phase_results=phase_results,
            verification_outcomes=verification_outcomes,
            artifact_references=artifact_references,
        )
        snapshot_reference = (
            SnapshotReference(**session["snapshot_reference"])
            if session["snapshot_reference"]
            else None
        )
        result = orchestrator._build_result(
            mode=mode,
            permissions=permissions,
            approval_mode=approval_mode,
            command_policy=command_policy,
            scope_manager=scope_manager,
            verification=verification,
            interpretation=interpretation,
            repo_index_summary=repo_index_summary,
            change_preview=change_preview,
            llm_summary=llm_summary,
            llm_review_summary=llm_review_summary,
            plan=plan,
            phase_policies=[
                phase_policy_from_dict(item)
                for item in session.get("phase_policies", [])
            ],
            context_package=context_package,
            workspace=workspace,
            terminal=terminal,
            artifact_references=artifact_references,
            phase_results=phase_results,
            step_results=step_results,
            initial_execution_result=initial_execution_result,
            failure_classification=failure_classification,
            repair_summary=repair_summary,
            final_execution_result=final_execution_result,
            snapshot_reference=snapshot_reference,
        )
        orchestrator._attach_result_artifact(
            artifact_manager, artifact_references, result
        )
        session["result"] = result.to_dict()
        session["context_package"] = context_package.to_dict()
        session["artifact_references"] = [
            artifact.to_dict() for artifact in result.artifact_references
        ]
        session["phase_results"] = [phase.to_dict() for phase in result.phase_results]
        session["step_results"] = [step.to_dict() for step in result.step_results]
        session["files_read"] = list(result.files_read)
        session["files_modified"] = list(result.files_modified)
        session["blocked_actions"] = [
            action.to_dict() for action in result.blocked_actions
        ]
        session["commands_run"] = [command.to_dict() for command in result.commands_run]
        session["initial_execution_result"] = (
            result.initial_execution_result.to_dict()
            if result.initial_execution_result
            else None
        )
        session["final_execution_result"] = result.final_execution_result.to_dict()
        session["failure_classification"] = (
            result.failure_classification.to_dict()
            if result.failure_classification
            else None
        )
        session["repair_summary"] = (
            result.repair_summary.to_dict() if result.repair_summary else None
        )
        session["status"] = (
            RunStatus.COMPLETED.value
            if result.final_execution_result.success
            else RunStatus.FAILED.value
        )
        session["pending_phase"] = None
        session["activity"].append(
            "Execution completed successfully."
            if result.final_execution_result.success
            else "Execution ended with a failure summary."
        )

        # If there are queued tasks, reset session state to process the next one
        if session.get("queued_tasks"):
            next_task = session["queued_tasks"].pop(0)
            session["activity"].append(f"Starting next queued task: {next_task}")

            # Update root interpretation with new task
            # For simplicity, we reset many fields to allow Orchestrator logic to re-run
            root_interpretation = interpretation_from_dict(session["task_interpretation"])
            root_interpretation.raw_task = next_task
            root_interpretation.summary = next_task
            root_interpretation.action = TaskAction.COMPLEX # Assume complex for now
            root_interpretation.subtasks = None
            root_interpretation.compressed_history = None
            root_interpretation.compressed_subtask_count = 0

            session["task_interpretation"] = root_interpretation.to_dict()
            session["status"] = RunStatus.RUNNING.value
            session["current_subtask_index"] = -1
            session["completed_subtask_indices"] = []
            session["phase_order"] = []
            session["next_phase_index"] = 0

            self._persist_session(session)
            self._start_worker(session["id"])
            return

        self._persist_session(session)

    def _build_success_execution_result(self, session: dict) -> ExecutionResult:
        interpretation = interpretation_from_dict(session["task_interpretation"])
        verification_outcome = None
        verification_steps = [
            item
            for item in session["step_results"]
            if item["step_type"] == StepType.VERIFICATION.value and item.get("details")
        ]
        if verification_steps:
            verification_outcome = verification_steps[-1]["details"]
        if interpretation.action == TaskAction.CREATE_FILE:
            return ExecutionResult(
                success=True,
                message=f"Created '{interpretation.target_path}'.",
                details=verification_outcome or {},
            )
        if interpretation.action == TaskAction.MODIFY_FILE:
            return ExecutionResult(
                success=True,
                message=f"Modified '{interpretation.target_path}'.",
                details=verification_outcome or {},
            )
        if interpretation.action == TaskAction.RUN_COMMAND:
            return ExecutionResult(
                success=True,
                message="Command executed.",
                details=verification_outcome or (session["last_command_result"] or {}),
            )
        restored = session.get("last_restore_reference") or {}
        snapshot_id = restored.get(
            "snapshot_id", interpretation.snapshot_id or "latest"
        )
        return ExecutionResult(
            success=True,
            message=f"Restored snapshot '{snapshot_id}'.",
            details=verification_outcome or restored,
        )
