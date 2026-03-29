from __future__ import annotations

from models import (
    ApprovalMode,
    ArtifactReference,
    ChangePreview,
    CommandPolicyMode,
    LLMStructuredResult,
    Mode,
    PhaseResult,
    Plan,
    ProjectMemoryContext,
    RepoIndexSummary,
    TaskContextPackage,
    VerificationConfig,
)


class ContextBuilder:
    def build(
        self,
        task: str,
        mode: Mode,
        approval_mode: ApprovalMode,
        effective_scope: dict,
        protected_paths: list[str],
        verification: VerificationConfig,
        command_policy: CommandPolicyMode,
        project_memory: ProjectMemoryContext | None,
        repo_index_summary: RepoIndexSummary | None,
        plan: Plan | None,
        phase_policies,
        change_preview: ChangePreview | None,
        llm_summary: LLMStructuredResult | None,
        llm_review_summary: LLMStructuredResult | None,
        phase_results: list[PhaseResult],
        verification_outcomes: list[dict[str, object]],
        artifact_references: list[ArtifactReference],
    ) -> TaskContextPackage:
        return TaskContextPackage(
            task=task,
            mode=mode.value,
            approval_mode=approval_mode.value,
            effective_scope=effective_scope,
            protected_paths=protected_paths,
            verification=verification.to_dict(),
            command_policy=command_policy.value,
            project_memory=project_memory.to_dict() if project_memory else None,
            repo_index_summary=(
                repo_index_summary.to_dict() if repo_index_summary else None
            ),
            prior_generated_plan=plan.to_dict() if plan else None,
            phase_policies=[
                (
                    policy.to_dict()
                    if hasattr(policy, "to_dict")
                    else (
                        policy.__dict__ if hasattr(policy, "__dict__") else dict(policy)
                    )
                )
                for policy in phase_policies
            ],
            change_preview_summary=change_preview.to_dict() if change_preview else None,
            llm_summary=llm_summary.to_dict() if llm_summary else None,
            llm_review_summary=(
                llm_review_summary.to_dict() if llm_review_summary else None
            ),
            prior_phase_results=[phase.to_dict() for phase in phase_results],
            prior_verification_outcomes=verification_outcomes,
            artifact_references=artifact_references,
        )
