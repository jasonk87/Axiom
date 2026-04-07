import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import {
  archiveRun,
  unarchiveRun,
  approvePhase,
  approvePlan,
  approveDecomposition,
  approveImplementation,
  cancelRun,
  createProject,
  declinePhase,
  declinePlan,
  declineDecomposition,
  declineImplementation,
  fetchArtifact,
  fetchLLMSettings,
  fetchProjects,
  fetchRun,
  fetchRuns,
  prepareRun,
  setActiveProject,
  updateLLMSettings,
} from "./api";
import { InspectModal } from "./components/InspectModal";
import { SessionStepCard, type SessionStepStatus } from "./components/SessionStepCard";
import { useLocalStorageState } from "./hooks/useLocalStorageState";
import type { AxiomProject, AxiomSession, LLMSettings, LLMStructuredSummary, PrepareRunRequest, PreviewFileChange } from "./types";

const MIN_LEFT = 220;
const MAX_LEFT = 420;

type LayoutState = {
  leftWidth: number;
  leftCollapsed: boolean;
  advancedOpen: boolean;
  projectPickerManualOpen: boolean;
};

type StreamStep = {
  id: string;
  key: string;
  title: string;
  summary: string;
  status: SessionStepStatus;
  detail: ReactNode;
  actionLabel?: string;
  inspectTitle?: string;
  inspectContent?: unknown;
  artifactPath?: string;
  actionKind?: "inspect" | "retry" | "artifact";
};

const DEFAULT_LAYOUT: LayoutState = {
  leftWidth: 280,
  leftCollapsed: false,
  advancedOpen: false,
  projectPickerManualOpen: false,
};

const DEFAULT_LLM_SETTINGS: LLMSettings = {
  enabled: false,
  review_enabled: true,
  provider: "ollama",
  base_url: "http://127.0.0.1:11434",
  model: "gemma-4",
  timeout_seconds: 20,
  retry_limit: 2,
  temperature: 0.1,
  compression_enabled: true,
  compression_threshold: 5,
  embedding_model: "nomic-embed-text",
};

function splitList(raw: string): string[] {
  return raw.split(/[\n,]/).map((value) => value.trim()).filter(Boolean);
}

function prettyJson(value: unknown): string {
  return JSON.stringify(value, null, 2);
}

function lastPathSegment(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts.length > 0 ? parts[parts.length - 1] : path;
}

function humanizeStatus(value?: string): string {
  return (value ?? "").split("_").join(" ");
}

function isActiveStatus(status?: string): boolean {
  return ["running", "cancelling", "auto_running_read_only_phase"].includes(status ?? "");
}

function sessionTone(status?: string): SessionStepStatus {
  if (!status) return "pending";
  if (status === "completed") return "complete";
  if (["running", "cancelling", "auto_running_read_only_phase"].includes(status)) return "running";
  if (["failed", "cancelled", "declined_plan", "declined_phase", "recovered_after_restart"].includes(status)) return "warning";
  return "pending";
}

function relativeTime(value?: string): string {
  if (!value) return "";
  const date = new Date(value);
  const diffMinutes = Math.round((Date.now() - date.getTime()) / 60000);
  if (diffMinutes <= 0) return "now";
  if (diffMinutes < 60) return `${diffMinutes}m`;
  if (diffMinutes < 1440) return `${Math.round(diffMinutes / 60)}h`;
  return `${Math.round(diffMinutes / 1440)}d`;
}

function diffCount(session: AxiomSession): string | null {
  const previewCount = session.change_preview?.file_changes?.length ?? 0;
  const modifiedCount = session.files_modified?.length ?? 0;
  const count = Math.max(previewCount, modifiedCount);
  return count > 0 ? `+${count}` : null;
}

function pluralize(count: number, singular: string, plural?: string): string {
  return `${count} ${count === 1 ? singular : (plural ?? `${singular}s`)}`;
}

function inferRunAppTask(session: AxiomSession | null): string {
  const summary = session?.result?.repo_index_summary;
  const likelyEntry =
    summary && typeof summary === "object" && "likely_entry_files" in summary
      ? ((summary as { likely_entry_files?: string[] }).likely_entry_files?.[0] ?? "")
      : "";

  if (likelyEntry.endsWith(".py")) return `Run command python ${likelyEntry}`;
  if (likelyEntry === "package.json") return "Run command npm run dev";
  return "Run command python main.py";
}

function inferCommitTask(): string {
  return 'Run command git add -A && git commit -m "Jules update"';
}

function previewSummary(change: PreviewFileChange): string {
  const before = change.before_exists ? "existing file" : "new file";
  return `${before} - ${change.blocked ? "blocked" : change.action}`;
}

function modelChipLabel(settings: LLMSettings | null): string {
  if (!settings) return "AI settings";
  if (!settings.enabled) return "AI off";
  return `${settings.provider} / ${settings.model}`;
}

function buildPreviewDetails(changes: PreviewFileChange[] | undefined): ReactNode {
  if (!changes || changes.length === 0) {
    return <div className="detail-note">No previewed file changes yet.</div>;
  }

  return (
    <div className="change-stack">
      {changes.map((change) => (
        <section key={change.path} className="change-card">
          <div className="change-card-header">
            <div>
              <strong>{change.path}</strong>
              <p>{previewSummary(change)}</p>
            </div>
            <span className={change.blocked ? "mini-pill warning" : "mini-pill"}>{change.blocked ? "blocked" : "preview"}</span>
          </div>
          <div className="provenance-row">
            <span>source {String(change.origin?.source ?? "snapshot")}</span>
            <span>before {String(change.content_hashes?.before_sha256 ?? "none").slice(0, 12)}</span>
            <span>after {String(change.content_hashes?.after_sha256 ?? "none").slice(0, 12)}</span>
          </div>
          {change.diff_preview ? <pre>{change.diff_preview}</pre> : null}
          {!change.diff_preview ? (
            <div className="before-after-grid">
              <div>
                <div className="inline-label">Before</div>
                <pre>{change.before_preview ?? "(new file)"}</pre>
              </div>
              <div>
                <div className="inline-label">After</div>
                <pre>{change.after_preview ?? "(empty)"}</pre>
              </div>
            </div>
          ) : null}
        </section>
      ))}
    </div>
  );
}

export default function App() {
  const [projectPath, setProjectPath] = useState("");
  const [projectName, setProjectName] = useState("");
  const [mode, setMode] = useState("implement");
  const [approvalMode, setApprovalMode] = useState("normal");
  const [verificationProfile, setVerificationProfile] = useState("basic");
  const [verificationCommands, setVerificationCommands] = useState("");
  const [commandPolicy, setCommandPolicy] = useState("permissive");
  const [previewChanges, setPreviewChanges] = useState(true);
  const [buildRepoIndex, setBuildRepoIndex] = useState(true);
  const [autoRepair, setAutoRepair] = useState(false);
  const [scopeText, setScopeText] = useState("");
  const [protectedText, setProtectedText] = useState(".env, config");
  const [task, setTask] = useState("");
  const [session, setSession] = useState<AxiomSession | null>(null);
  const [recentRuns, setRecentRuns] = useState<AxiomSession[]>([]);
  const [projects, setProjects] = useState<AxiomProject[]>([]);
  const [activeProjectId, setActiveProjectId] = useLocalStorageState<string | null>("axiom-ui-active-project-v1", null);
  const [llmSettings, setLlmSettings] = useState<LLMSettings>(DEFAULT_LLM_SETTINGS);
  const [aiSettingsOpen, setAiSettingsOpen] = useState(false);
  const [projectModalOpen, setProjectModalOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [controlReason, setControlReason] = useState("");
  const [inspectModal, setInspectModal] = useState<{ title: string; content: unknown } | null>(null);
  const [layout, setLayout] = useLocalStorageState<LayoutState>("axiom-ui-session-layout-v3", DEFAULT_LAYOUT);
  const [expandedSteps, setExpandedSteps] = useLocalStorageState<Record<string, boolean>>("axiom-ui-step-expansion-v3", {});
  const [activeSessionId, setActiveSessionId] = useLocalStorageState<string | null>("axiom-ui-active-session-v3", null);
  const [showArchived, setShowArchived] = useLocalStorageState<boolean>("axiom-ui-show-archived-v1", false);
  const [visibleStepCount, setVisibleStepCount] = useState(0);
  const [streamSignature, setStreamSignature] = useState("");
  const resizeRef = useRef<{ start: number; size: number } | null>(null);

  async function refreshRuns() {
    try {
      const result = await fetchRuns();
      setRecentRuns(result.runs);
      if (!session && activeSessionId && result.runs.some((run) => run.id === activeSessionId)) {
        const restored = await fetchRun(activeSessionId);
        setSession(restored);
        setProjectPath(restored.project_root);
        setActiveProjectId(restored.project_id ?? null);
      }
    } catch {
      // Keep current state visible.
    }
  }

  async function refreshProjects() {
    try {
      const result = await fetchProjects();
      setProjects(result.projects);
      const nextActiveProjectId = result.active_project_id ?? result.projects[0]?.id ?? null;
      if (nextActiveProjectId) {
        setActiveProjectId(nextActiveProjectId);
        const activeProject = result.projects.find((item) => item.id === nextActiveProjectId);
        if (activeProject) {
          setProjectPath(activeProject.root_path);
        }
      } else {
        setActiveProjectId(null);
        setProjectPath("");
      }
    } catch {
      // Keep current project state visible.
    }
  }

  async function refreshLLMSettings() {
    try {
      const result = await fetchLLMSettings();
      setLlmSettings(result);
    } catch {
      // Keep the current local settings visible if refresh fails.
    }
  }

  useEffect(() => {
    void refreshRuns();
    void refreshProjects();
    void refreshLLMSettings();
  }, []);

  useEffect(() => {
    if (!session?.id || !isActiveStatus(session.status)) {
      return undefined;
    }

    const handle = window.setInterval(async () => {
      try {
        const next = await fetchRun(session.id);
        setSession(next);
        setActiveSessionId(next.id);
        void refreshRuns();
      } catch {
        // Ignore transient polling failures.
      }
    }, 700);

    return () => window.clearInterval(handle);
  }, [session?.id, session?.status, setActiveSessionId]);

  useEffect(() => {
    function onMouseMove(event: MouseEvent) {
      if (!resizeRef.current) return;
      setLayout((current) => ({
        ...current,
        leftWidth: Math.min(MAX_LEFT, Math.max(MIN_LEFT, resizeRef.current!.size + (event.clientX - resizeRef.current!.start))),
      }));
    }

    function onMouseUp() {
      resizeRef.current = null;
      document.body.classList.remove("is-resizing-horizontal");
    }

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [setLayout]);

  function beginResize(start: number, size: number) {
    resizeRef.current = { start, size };
    document.body.classList.add("is-resizing-horizontal");
  }

  function namespacedStepKey(stepId: string): string {
    return `${session?.id ?? "draft"}:${stepId}`;
  }

  function toggleStep(id: string) {
    setExpandedSteps((current) => ({
      ...current,
      [id]: !current[id],
    }));
  }

  async function handleRun() {
    if (!activeProjectId) {
      setError("Open a project folder before starting a task.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const payload: PrepareRunRequest = {
        projectId: activeProjectId,
        projectPath,
        mode,
        task,
        approvalMode,
        verificationProfile,
        verificationCommands: splitList(verificationCommands),
        commandPolicy,
        previewChanges,
        buildRepoIndex,
        autoRepair,
        scopePaths: splitList(scopeText),
        protectedPaths: splitList(protectedText),
      };
      const next = await prepareRun(payload);
      setSession(next);
      setActiveSessionId(next.id);
      await refreshRuns();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleSelectSession(runId: string) {
    if (!runId) return;
    setBusy(true);
    setError(null);
    try {
      const next = await fetchRun(runId);
      setSession(next);
      setActiveSessionId(next.id);
      setProjectPath(next.project_root);
      setActiveProjectId(next.project_id ?? null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleApprovePlan() {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      setSession(await approvePlan(session.id));
      await refreshRuns();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleApprovePhase() {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      setSession(await approvePhase(session.id));
      await refreshRuns();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleApproveDecomposition() {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      setSession(await approveDecomposition(session.id));
      await refreshRuns();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleApproveImplementation() {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      setSession(await approveImplementation(session.id));
      await refreshRuns();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleDeclinePlan() {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      setSession(await declinePlan(session.id, controlReason || undefined));
      await refreshRuns();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleDeclinePhase() {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      setSession(await declinePhase(session.id, controlReason || undefined));
      await refreshRuns();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleDeclineDecomposition() {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      setSession(await declineDecomposition(session.id, controlReason || undefined));
      await refreshRuns();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleDeclineImplementation() {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      setSession(await declineImplementation(session.id, controlReason || undefined));
      await refreshRuns();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleCancelRun() {
    if (!session) return;
    setBusy(true);
    setError(null);
    try {
      setSession(await cancelRun(session.id, controlReason || undefined));
      await refreshRuns();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleSaveAISettings() {
    setBusy(true);
    setError(null);
    try {
      const updated = await updateLLMSettings(llmSettings);
      setLlmSettings(updated);
      setAiSettingsOpen(false);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateProject() {
    setBusy(true);
    setError(null);
    try {
      const created = await createProject(projectPath, projectName || undefined);
      setProjectModalOpen(false);
      setProjectName("");
      setActiveProjectId(created.id);
      setProjectPath(created.root_path);
      setSession(null);
      setActiveSessionId(null);
      await refreshProjects();
      await refreshRuns();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleSelectProject(projectId: string) {
    setBusy(true);
    setError(null);
    setSession(null);
    setActiveSessionId(null);
    try {
      const response = await setActiveProject(projectId);
      setProjects(response.projects);
      setActiveProjectId(response.active_project_id ?? projectId);
      const project = response.projects.find((item) => item.id === projectId);
      if (project) {
        setProjectPath(project.root_path);
      }
      const latestRun = recentRuns
        .filter((run) => run.project_id === projectId && !run.archived)
        .sort((a, b) => (b.updated_at ?? b.created_at ?? "").localeCompare(a.updated_at ?? a.created_at ?? ""))[0];
      if (latestRun) {
        const next = await fetchRun(latestRun.id);
        setSession(next);
        setActiveSessionId(next.id);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleArchiveSession(runId: string) {
    setBusy(true);
    try {
      await archiveRun(runId);
      await refreshRuns();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function handleUnarchiveSession(runId: string) {
    setBusy(true);
    try {
      await unarchiveRun(runId);
      await refreshRuns();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  async function openArtifact(path: string, label: string) {
    setBusy(true);
    setError(null);
    try {
      const artifact = await fetchArtifact(path);
      setInspectModal({ title: label, content: artifact.content });
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : String(caught));
    } finally {
      setBusy(false);
    }
  }

  function handleNewTask() {
    setError(null);
    setControlReason("");
    setTask("");
    if (!activeProjectId) {
      setSession(null);
      setActiveSessionId(null);
      return;
    }
    setSession(null);
    setActiveSessionId(null);
  }

  function openProjectPicker() {
    setProjectModalOpen(true);
    setError(null);
  }

  async function handleQuickOpenProject(project: { id: string; root: string; label: string }) {
    setProjectModalOpen(false);
    setProjectPath(project.root);
    setProjectName(project.label);
    await handleSelectProject(project.id);
  }

  const activeProject = useMemo(
    () => projects.find((project) => project.id === activeProjectId) ?? null,
    [activeProjectId, projects],
  );
  const hasProjects = projects.length > 0;
  const groupedProjects = useMemo(() => {
    return projects
      .map((project) => ({
        id: project.id,
        root: project.root_path,
        label: project.name,
        memory: project.memory,
        runs: recentRuns
          .filter((run) => run.project_id === project.id && (showArchived || !run.archived))
          .sort((a, b) => (b.updated_at ?? b.created_at ?? "").localeCompare(a.updated_at ?? a.created_at ?? "")),
        hasArchived: recentRuns.some((run) => run.project_id === project.id && run.archived),
      }))
      .sort((a, b) => (b.runs[0]?.updated_at ?? b.root).localeCompare(a.runs[0]?.updated_at ?? a.root));
  }, [projects, recentRuns]);
  const recentProjects = useMemo(() => groupedProjects.slice(0, 6), [groupedProjects]);

  function buildLLMStreamSteps(summary: LLMStructuredSummary | null | undefined, kind: "plan" | "review"): StreamStep[] {
    if (!summary?.events?.length) {
      return [];
    }
    return summary.events.map((event, index) => {
      const llmStatus: SessionStepStatus =
        event.status === "failed"
          ? "warning"
          : event.status === "running"
            ? "running"
            : "complete";
      const title =
        event.stage === "apply_project_context"
          ? "Applying project context..."
          : kind === "plan"
            ? event.stage === "query_model"
              ? "Asking Jules for plan..."
              : event.stage === "validate_response"
                ? "Validating plan response..."
                : event.stage === "retry_output"
                  ? `Retrying plan output (attempt ${event.attempt})...`
                  : event.stage === "fallback"
                    ? "Using system fallback plan..."
                    : "Jules planning"
            : event.stage === "query_model"
              ? "Reviewing plan for overbuild and scope drift..."
              : event.stage === "validate_response"
                ? "Validating review response..."
                : event.stage === "retry_output"
                  ? `Retrying review output (attempt ${event.attempt})...`
                  : event.stage === "fallback"
                    ? "Review fallback used..."
                    : "Jules review";
      return {
        id: `${kind}-llm-${index}`,
        key: namespacedStepKey(`${kind}-llm-${index}`),
        title,
        summary: event.summary,
        status: llmStatus,
        detail: (
          <div className="detail-stack">
            {event.stage === "apply_project_context" ? (
              <>
                <div className="detail-note">Project memory is advisory context only. Scope, permissions, approval, and execution rules remain system-enforced.</div>
                <pre>{prettyJson(event.details?.project_memory ?? {})}</pre>
              </>
            ) : (
              <pre>{prettyJson(event.details ?? {})}</pre>
            )}
          </div>
        ),
        actionLabel: event.artifact_reference ? "Inspect" : undefined,
        actionKind: event.artifact_reference ? "artifact" : "inspect",
        artifactPath: event.artifact_reference?.path,
        inspectTitle: kind === "plan" ? "Local plan activity" : "Local review activity",
        inspectContent: event,
      };
    });
  }

  const commandResults = session?.commands_run ?? [];
  const artifactReferences = session?.artifact_references ?? [];
  const pendingApproval = session?.status === "awaiting_plan_approval" || session?.status === "awaiting_phase_approval" || session?.status === "awaiting_decomposition_approval" || session?.status === "awaiting_implementation_approval";
  const commandStepStatus: SessionStepStatus =
    commandResults.some((command) => command.cancelled || !command.success)
      ? "warning"
      : commandResults.length > 0
        ? "complete"
        : session?.status && isActiveStatus(session.status)
          ? "running"
          : "pending";

  const streamSteps = useMemo<StreamStep[]>(() => {
    const steps: StreamStep[] = [];

    if (error) {
      steps.push({
        id: "ui-error",
        key: `${session?.id ?? "draft"}:ui-error:${error}`,
        title: "Request failed",
        summary: "The backend request failed before the stream could advance.",
        status: "warning",
        actionLabel: "Retry",
        actionKind: "retry",
        inspectTitle: "Error details",
        inspectContent: { error },
        detail: (
          <div className="error-step-body">
            <p>{error}</p>
            <div className="detail-note">Retry will resubmit the current bottom-bar task with the current controls.</div>
          </div>
        ),
      });
    }

    if (!session) {
      return steps;
    }

    steps.push(...buildLLMStreamSteps(session.llm_summary, "plan"));

    steps.push({
      id: "scan",
      key: namespacedStepKey("scan"),
      title: "Scanning project files...",
      summary: session.repo_index_summary?.generated
        ? `Checked ${session.repo_index_summary.total_files} files and prepared project context.`
        : "Preparing workspace scope, protected paths, and task context.",
      status: session.repo_index_summary?.generated ? "complete" : isActiveStatus(session.status) ? "running" : "pending",
      detail: (
        <div className="detail-stack">
          <div className="detail-note">Scope: {session.effective_scope?.description ?? "whole project"}</div>
          <pre>{prettyJson(session.repo_index_summary ?? { protected_paths: session.protected_paths })}</pre>
        </div>
      ),
      actionLabel: "Inspect",
      actionKind: "inspect",
      inspectTitle: "Project Scan",
      inspectContent: {
        scope: session.effective_scope,
        protected_paths: session.protected_paths,
        repo_index_summary: session.repo_index_summary,
      },
    });

    if (session.subtasks && session.subtasks.length > 0) {
      steps.push({
        id: "decomposition",
        key: namespacedStepKey("decomposition"),
        title: "Decomposing task...",
        summary: `Broke the task down into ${session.subtasks.length} subtasks.`,
        status: "complete",
        detail: (
          <div className="plan-step-list">
            {session.subtasks.map((subtask, i) => (
              <div key={i} className={`plan-step-card ${session.current_subtask_index === i ? "active-subtask" : ""}`}>
                <div className="plan-step-meta">
                  <span>{subtask.action}</span>
                </div>
                <strong>{subtask.description}</strong>
                {subtask.target_path && <p>Target: {subtask.target_path}</p>}
                {subtask.command && <p>Command: {subtask.command}</p>}
                {subtask.result_summary && (
                  <div className="detail-note success">
                    Result: {subtask.result_summary}
                  </div>
                )}
              </div>
            ))}
          </div>
        ),
        actionLabel: "Inspect Subtasks",
        actionKind: "inspect",
        inspectTitle: "Subtasks",
        inspectContent: session.subtasks,
      });

      if (session.current_subtask_content || session.current_subtask_command) {
         steps.push({
            id: "implementation",
            key: namespacedStepKey("implementation"),
            title: `Generated implementation for subtask ${(session.current_subtask_index ?? 0) + 1}`,
            summary: "Awaiting code/command review before execution.",
            status: "complete",
            detail: (
              <div className="plan-step-list">
                <div className="plan-step-card">
                  <strong>Implementation Details</strong>
                  <pre>{session.current_subtask_content ?? session.current_subtask_command}</pre>
                </div>
              </div>
            ),
         });
      }
    } else {
      steps.push({
        id: "plan",
        key: namespacedStepKey("plan"),
        title: "Generating a step-by-step plan...",
        summary: session.plan
          ? `Created a ${session.plan.steps.length}-step plan for the current task.`
          : "Building a task-aware plan now.",
        status: session.plan ? "complete" : isActiveStatus(session.status) || session.status === "awaiting_plan_approval" ? "running" : "pending",
        detail: (
          <div className="plan-step-list">
            {session.plan?.steps?.map((step) => (
              <div key={step.id} className="plan-step-card">
                <div className="plan-step-meta">
                  <span>{step.phase}</span>
                  <span>{step.type}</span>
                  <span>{step.approval_hint}</span>
                </div>
                <strong>{step.title}</strong>
                <p>{step.description}</p>
              </div>
            )) ?? <div className="detail-note">Waiting for plan details.</div>}
          </div>
        ),
        actionLabel: session.plan ? "Inspect Plan" : undefined,
        actionKind: "inspect",
        inspectTitle: "Plan",
        inspectContent: session.plan,
      });
    }

    steps.push(...buildLLMStreamSteps(session.llm_review_summary, "review"));

    if (session.llm_review_summary) {
      const acceptedPayload = session.llm_review_summary.accepted_payload ?? {};
      const verdict = typeof acceptedPayload.verdict === "string" ? acceptedPayload.verdict : "fallback";
      const findings = Array.isArray(acceptedPayload.findings) ? acceptedPayload.findings : [];
      const reviewStatus: SessionStepStatus =
        session.llm_review_summary.accepted
          ? verdict === "accept"
            ? "complete"
            : "warning"
          : session.llm_review_summary.fallback_used
            ? "warning"
            : "pending";
      steps.push({
        id: "plan-review",
        key: namespacedStepKey("plan-review"),
        title: "Reviewing the plan...",
        summary: session.llm_review_summary.accepted
          ? `${String(acceptedPayload.summary ?? session.llm_review_summary.final_message)}`
          : session.llm_review_summary.final_message,
        status: reviewStatus,
        detail: <pre>{prettyJson(session.llm_review_summary.accepted_payload ?? session.llm_review_summary)}</pre>,
        actionLabel: findings.length > 0 || session.llm_review_summary.artifact_references.length > 0 ? "Inspect Review" : undefined,
        actionKind: "inspect",
        inspectTitle: "Plan Review",
        inspectContent: session.llm_review_summary,
      });
    }

    const modifyPhase = session.phase_results?.find((phase) => phase.phase === "modify");
    const modifyStatus: SessionStepStatus =
      modifyPhase?.status === "completed"
        ? "complete"
        : modifyPhase?.status === "failed" || (session.blocked_actions?.length ?? 0) > 0
          ? "warning"
          : session.status === "running" || session.status === "auto_running_read_only_phase"
            ? "running"
            : "pending";

    steps.push({
      id: "modify",
      key: namespacedStepKey("modify"),
      title: "Modifying files...",
      summary:
        session.files_modified && session.files_modified.length > 0
          ? `Modified ${session.files_modified.length} file(s) in the workspace.`
          : session.change_preview?.file_changes?.length
            ? `Prepared ${session.change_preview.file_changes.length} previewed file change(s).`
            : "Waiting to apply requested changes.",
      status: modifyStatus,
      detail: (
        <div className="detail-stack">
          {buildPreviewDetails(session.change_preview?.file_changes)}
          {session.blocked_actions?.length ? (
            <div className="detail-warning">
              {session.blocked_actions.map((item: { path?: string; reason?: string }, index: number) => (
                <div key={`${item.path ?? "blocked"}-${index}`}>{`${item.path ?? "path"}: ${item.reason ?? "blocked"}`}</div>
              ))}
            </div>
          ) : null}
        </div>
      ),
      actionLabel: session.change_preview?.file_changes?.length ? "Inspect Changes" : undefined,
      actionKind: "inspect",
      inspectTitle: "Changes",
      inspectContent: {
        preview: session.change_preview,
        files_modified: session.files_modified,
        blocked_actions: session.blocked_actions,
      },
    });

    steps.push({
      id: "terminal",
      key: namespacedStepKey("terminal"),
      title: "Terminal output",
      summary:
        commandResults.length > 0
          ? `${commandResults.length} command result(s) captured inline for this task.`
          : "Command output will appear inline here when Jules runs verification or shell work.",
      status: commandStepStatus,
      detail: (
        <div className="command-stack">
          {commandResults.length > 0 ? commandResults.map((command, index) => (
            <div key={`${command.command}-${index}`} className="command-card">
              <div className="command-card-header">
                <strong>{command.command}</strong>
                <span className={command.success ? "mini-pill success" : "mini-pill warning"}>
                  {command.success ? "ok" : command.cancelled ? "cancelled" : "failed"}
                </span>
              </div>
              <p>{command.summary}</p>
              <pre>{command.stdout || command.stderr || "(no output)"}</pre>
            </div>
          )) : <div className="detail-note">No terminal activity yet.</div>}
        </div>
      ),
      actionLabel: commandResults.length > 0 ? "Inspect Logs" : undefined,
      actionKind: "inspect",
      inspectTitle: "Terminal Output",
      inspectContent: commandResults,
    });

    const verifyPhase = session.phase_results?.find((phase) => phase.phase === "verify");
    const verifyStatus: SessionStepStatus =
      verifyPhase?.status === "completed"
        ? "complete"
        : verifyPhase?.status === "failed" || session.failure_classification
          ? "warning"
          : session.step_results?.some((step) => step.step_type === "verification") || isActiveStatus(session.status)
            ? "running"
            : "pending";

    steps.push({
      id: "verify",
      key: namespacedStepKey("verify"),
      title: "Running verification...",
      summary: verifyPhase?.status === "completed"
        ? "Verification finished and the results are ready to inspect."
        : "Verification stays attached to the stream and will update as commands finish.",
      status: verifyStatus,
      detail: (
        <div className="detail-stack">
          <pre>{prettyJson({
            phase_results: session.phase_results,
            verification_steps: session.step_results?.filter((step) => step.step_type === "verification"),
          })}</pre>
        </div>
      ),
      actionLabel: "Inspect Verification",
      actionKind: "inspect",
      inspectTitle: "Verification",
      inspectContent: {
        phase_results: session.phase_results,
        step_results: session.step_results?.filter((step) => step.step_type === "verification"),
      },
    });

    const hasIssue = Boolean(session.failure_classification || session.repair_summary || session.status === "recovered_after_restart");
    steps.push({
      id: "review",
      key: namespacedStepKey("review"),
      title: hasIssue ? "Reviewing failure state..." : "Reviewing changes...",
      summary: hasIssue
        ? "Jules captured failure, repair, or recovery context for review."
        : "Checking whether the result matches intent without scope drift.",
      status: hasIssue ? "warning" : session.final_execution_result ? "complete" : "pending",
      detail: (
        <div className="detail-stack">
          {session.failure_classification ? <pre>{prettyJson(session.failure_classification)}</pre> : null}
          {session.repair_summary ? <pre>{prettyJson(session.repair_summary)}</pre> : null}
          {artifactReferences.length > 0 ? (
            <div className="artifact-chip-row">
              {artifactReferences.map((artifact) => (
                <button key={artifact.path} className="artifact-pill" onClick={() => openArtifact(artifact.path, artifact.label)}>
                  {artifact.label}
                </button>
              ))}
            </div>
          ) : (
            <div className="detail-note">Artifacts will appear here when the backend records them.</div>
          )}
        </div>
      ),
      actionLabel: "Inspect Review",
      actionKind: "inspect",
      inspectTitle: "Review Summary",
      inspectContent: {
        failure: session.failure_classification,
        repair: session.repair_summary,
        artifacts: artifactReferences,
      },
    });

    steps.push({
      id: "ready",
      key: namespacedStepKey("ready"),
      title: "Ready for review",
      summary: session.final_execution_result?.message ?? "Awaiting execution or approval to continue.",
      status: session.status === "completed" ? "complete" : sessionTone(session.status),
      detail: <pre>{prettyJson(session.result ?? session.final_execution_result ?? session)}</pre>,
      actionLabel: "Inspect State",
      actionKind: "inspect",
      inspectTitle: "Run State",
      inspectContent: session,
    });

    return steps;
  }, [artifactReferences, commandResults, commandStepStatus, error, session]);

  useEffect(() => {
    const signature = streamSteps.map((step) => `${step.key}:${step.status}`).join("|");
    const nextCount = streamSteps.length;
    if (signature !== streamSignature) {
      setStreamSignature(signature);
      setVisibleStepCount(0);
      return;
    }
    if (visibleStepCount >= nextCount) return;
    const handle = window.setTimeout(() => {
      setVisibleStepCount((current) => Math.min(current + 1, nextCount));
    }, 120);
    return () => window.clearTimeout(handle);
  }, [streamSteps, streamSignature, visibleStepCount]);

  const visibleSteps = streamSteps.slice(0, visibleStepCount);
  const canShowActionButtons = session?.status === "completed" && session.final_execution_result?.success;
  const sessionStatusLabel = humanizeStatus(session?.status) || "idle";
  const noProjectSelected = !activeProjectId;
  const emptyProjectState = !hasProjects;

  return (
    <div
      className="codex-shell interactive-shell"
      style={{
        gridTemplateColumns: layout.leftCollapsed ? "64px 8px minmax(0, 1fr)" : `${layout.leftWidth}px 8px minmax(0, 1fr)`,
      }}
    >
      <aside className={layout.leftCollapsed ? "session-sidebar collapsed" : "session-sidebar"}>
        <div className="sidebar-top">
          <div className="brand-row">
            <div className="brand-mark">J</div>
            {!layout.leftCollapsed ? <div className="brand-wordmark">Jules</div> : null}
            <button className="ghost-button" onClick={() => setLayout((current) => ({ ...current, leftCollapsed: !current.leftCollapsed }))}>
              {layout.leftCollapsed ? ">" : "<"}
            </button>
          </div>
          {!layout.leftCollapsed ? (
            <>
              <button className="new-task-button" onClick={handleNewTask}>+ New Task</button>
              <button className="ghost-button" onClick={openProjectPicker}>+ Open Folder</button>
            </>
          ) : (
            <>
              <button className="mini-icon-button" onClick={handleNewTask}>+</button>
              <button className="mini-icon-button" onClick={openProjectPicker}>P</button>
            </>
          )}
        </div>

        {!layout.leftCollapsed ? (
          <div className="sidebar-scroll">
            <div className="sidebar-section-header">
              <div className="sidebar-section-heading">Projects</div>
              <button
                className={`archive-toggle ${showArchived ? "active" : ""}`}
                onClick={() => setShowArchived(!showArchived)}
                title={showArchived ? "Hide archived sessions" : "Show archived sessions"}
              >
                {showArchived ? "Showing Archived" : "Show Archived"}
              </button>
            </div>
            {groupedProjects.length === 0 ? (
              <div className="sidebar-empty-state">
                <strong>Open a project folder to get started</strong>
                <p>Jules sessions live inside real rooted folders. Open one to begin a task.</p>
                <button className="ghost-button" onClick={openProjectPicker}>Open Folder</button>
              </div>
            ) : null}
            {groupedProjects.map((group) => (
              <section key={group.root} className="project-section">
                <div className="project-header">
                  <button className={group.id === activeProjectId ? "project-header-button active" : "project-header-button"} onClick={() => handleSelectProject(group.id)}>
                    <div className="project-header-topline">
                      <strong>{group.label}</strong>
                      <span className={group.id === activeProjectId ? "project-badge active" : "project-badge"}>
                        {group.id === activeProjectId ? "active" : pluralize(group.runs.length, "session")}
                      </span>
                    </div>
                    <span>{group.root}</span>
                  </button>
                </div>
                {group.memory?.project_summary ? <div className="project-memory-summary">{group.memory.project_summary}</div> : null}
                <div className="session-thread-list">
                  {group.runs.length === 0 ? <div className="session-thread-empty">No sessions yet</div> : null}
                  {group.runs.map((run) => (
                    <div key={run.id} className="session-thread-container">
                      <button
                        className={`${run.id === session?.id ? "session-thread active" : "session-thread"} ${run.archived ? "archived" : ""}`}
                        onClick={() => handleSelectSession(run.id)}
                      >
                        <div className="session-thread-main">
                          <div className="session-thread-title">{run.task_interpretation?.summary ?? "New task"}</div>
                          <div className="session-thread-meta">
                            <span>{relativeTime(run.updated_at ?? run.created_at)}</span>
                            <span>{humanizeStatus(run.status)}</span>
                          </div>
                        </div>
                        {diffCount(run) ? <span className="thread-diff-pill">{diffCount(run)}</span> : null}
                      </button>
                      <button
                        className="session-archive-button"
                        onClick={(e) => {
                          e.stopPropagation();
                          run.archived ? handleUnarchiveSession(run.id) : handleArchiveSession(run.id);
                        }}
                        title={run.archived ? "Unarchive session" : "Archive session"}
                      >
                        {run.archived ? "↺" : "×"}
                      </button>
                    </div>
                  ))}
                </div>
              </section>
            ))}
          </div>
        ) : (
          <div className="sidebar-collapsed-stack">
            {groupedProjects.slice(0, 6).map((group) => (
              <button key={group.root} className="sidebar-project-dot" title={group.label} onClick={() => handleSelectProject(group.id)}>
                {group.label.slice(0, 1).toUpperCase()}
              </button>
            ))}
          </div>
        )}

        {!layout.leftCollapsed ? <div className="sidebar-footer">Local-first sessions</div> : null}
      </aside>

      <div className="sidebar-resize" onMouseDown={(event) => !layout.leftCollapsed && beginResize(event.clientX, layout.leftWidth)} />

      <main className="workspace-shell session-workspace-shell">
        <section className="workspace-main-card session-main-card">
          <header className="session-header-card">
            <div className="session-header-copy">
              <div className={`status-orb ${busy && !session ? "running" : sessionTone(session?.status)}`} />
              <div>
                <h1>
                  {session?.task_interpretation?.summary ??
                    (emptyProjectState ? "Open a project folder to begin" : "Welcome to Jules")}
                </h1>
                <p>
                  {session?.task_interpretation?.raw_task ??
                    (emptyProjectState
                      ? "Projects are real rooted folders on disk. Open one, then create sessions inside it."
                      : activeProject
                        ? `Working inside ${activeProject.root_path}`
                        : "Select a project to continue.")}
                </p>
              </div>
            </div>
            <div className="session-chip-row">
              <span className="status-chip">{activeProject ? activeProject.name : "no project"}</span>
              <span className="status-chip">{mode}</span>
              <span className={`status-chip ${busy && !session ? "running" : sessionTone(session?.status)}`}>{busy && !session ? "preparing" : sessionStatusLabel}</span>
              {session?.pending_phase ? <span className="status-chip pending">phase {session.pending_phase}</span> : null}
              {previewChanges ? <span className="status-chip">preview on</span> : null}
              <button className="status-chip status-chip-button" onClick={() => setAiSettingsOpen(true)}>
                {modelChipLabel(llmSettings)}
              </button>
            </div>
          </header>

          {pendingApproval ? (
            <div className="approval-inline-bar">
              <div className="approval-copy">
                <strong>{session?.status === "awaiting_plan_approval" ? "Plan approval required" : `Phase approval required: ${session?.pending_phase}`}</strong>
                <span>Execution stays paused until you explicitly continue, decline, or cancel.</span>
              </div>
              <input value={controlReason} onChange={(event) => setControlReason(event.target.value)} placeholder="Optional decline or cancel reason" />
              {session?.status === "awaiting_plan_approval" ? (
                <>
                  <button className={`primary-button ${busy ? "loading" : ""}`} onClick={handleApprovePlan} disabled={busy}>Approve Plan</button>
                  <button className="ghost-button strong" onClick={handleDeclinePlan} disabled={busy}>Decline Plan</button>
                </>
              ) : session?.status === "awaiting_decomposition_approval" ? (
                <>
                  <button className={`primary-button ${busy ? "loading" : ""}`} onClick={handleApproveDecomposition} disabled={busy}>Approve Tasks</button>
                  <button className="ghost-button strong" onClick={handleDeclineDecomposition} disabled={busy}>Decline</button>
                </>
              ) : session?.status === "awaiting_implementation_approval" ? (
                <>
                  <button className={`primary-button ${busy ? "loading" : ""}`} onClick={handleApproveImplementation} disabled={busy}>Approve Code</button>
                  <button className="ghost-button strong" onClick={handleDeclineImplementation} disabled={busy}>Decline Code</button>
                </>
              ) : (
                <>
                  <button className={`primary-button ${busy ? "loading" : ""}`} onClick={handleApprovePhase} disabled={busy}>Approve Phase</button>
                  <button className="ghost-button strong" onClick={handleDeclinePhase} disabled={busy}>Decline Phase</button>
                </>
              )}
              <button className="ghost-button strong" onClick={handleCancelRun} disabled={busy}>Cancel Run</button>
            </div>
          ) : null}

          <div className="stream-scroll interactive-stream-scroll">
            {emptyProjectState ? (
              <div className="empty-stream-state">
                <h2>Open a project folder to begin</h2>
                <p>Jules organizes sessions under real project roots. Choose a folder, then start a task inside that project.</p>
                <button className="primary-button" onClick={openProjectPicker}>Open Folder</button>
              </div>
            ) : visibleSteps.length === 0 ? (
              <div className="empty-stream-state">
                <h2>{busy ? "Waking up the session..." : noProjectSelected ? "Select a project to continue" : "Session-first by default"}</h2>
                <p>
                  {busy
                    ? "Jules is preparing workspace context and the first stream events."
                    : noProjectSelected
                      ? "Choose a project from the sidebar, then start a new task in that rooted workspace."
                      : "The stream will stay alive here, even before a task is running. Start with the bottom input bar."}
                </p>
              </div>
            ) : (
              <div className="stream-column">
                {visibleSteps.map((step) => (
                  <SessionStepCard
                    key={step.key}
                    id={step.key}
                    title={step.title}
                    summary={step.summary}
                    status={step.status}
                    expanded={Boolean(expandedSteps[step.key])}
                    onToggle={toggleStep}
                    actionLabel={step.actionLabel}
                    onAction={
                      step.actionKind === "retry"
                        ? handleRun
                        : step.actionKind === "artifact" && step.artifactPath
                          ? () => openArtifact(step.artifactPath ?? "", step.actionLabel ?? step.title)
                        : step.inspectTitle
                          ? () => setInspectModal({ title: step.inspectTitle ?? "", content: step.inspectContent })
                          : undefined
                    }
                  >
                    {step.detail}
                  </SessionStepCard>
                ))}
              </div>
            )}
          </div>

          {canShowActionButtons ? (
            <div className="context-action-row">
              <button className="primary-button large" onClick={() => setTask(inferRunAppTask(session))}>Run App</button>
              <button className="ghost-button large strong" onClick={() => setTask(inferCommitTask())}>Commit Changes</button>
            </div>
          ) : null}

          <div className="composer-shell fixed-composer-shell">
            <button className="advanced-toggle-button" onClick={() => setLayout((current) => ({ ...current, advancedOpen: !current.advancedOpen }))}>
              {layout.advancedOpen ? "Hide advanced controls" : "Advanced controls"}
            </button>
            {layout.advancedOpen ? (
              <div className="advanced-control-panel">
                <div className="advanced-grid">
                  <label>
                    Project path
                    <input value={projectPath} onChange={(event) => setProjectPath(event.target.value)} />
                  </label>
                  <label>
                    Mode
                    <select value={mode} onChange={(event) => setMode(event.target.value)}>
                      <option value="conversation">conversation</option>
                      <option value="plan">plan</option>
                      <option value="implement">implement</option>
                    </select>
                  </label>
                  <label>
                    Approval mode
                    <select value={approvalMode} onChange={(event) => setApprovalMode(event.target.value)}>
                      <option value="normal">normal</option>
                      <option value="phased">phased</option>
                    </select>
                  </label>
                  <label>
                    Verification
                    <select value={verificationProfile} onChange={(event) => setVerificationProfile(event.target.value)}>
                      <option value="none">none</option>
                      <option value="basic">basic</option>
                      <option value="commands_only">commands_only</option>
                    </select>
                  </label>
                  <label>
                    Command policy
                    <select value={commandPolicy} onChange={(event) => setCommandPolicy(event.target.value)}>
                      <option value="permissive">permissive</option>
                      <option value="safe">safe</option>
                    </select>
                  </label>
                  <label>
                    Verification commands
                    <textarea rows={3} value={verificationCommands} onChange={(event) => setVerificationCommands(event.target.value)} />
                  </label>
                  <label>
                    Scope
                    <input value={scopeText} onChange={(event) => setScopeText(event.target.value)} />
                  </label>
                  <label>
                    Protected paths
                    <input value={protectedText} onChange={(event) => setProtectedText(event.target.value)} />
                  </label>
                  <div className="toggle-stack">
                    <label className="toggle">
                      <input type="checkbox" checked={previewChanges} onChange={(event) => setPreviewChanges(event.target.checked)} />
                      Preview changes
                    </label>
                    <label className="toggle">
                      <input type="checkbox" checked={buildRepoIndex} onChange={(event) => setBuildRepoIndex(event.target.checked)} />
                      Build repo index
                    </label>
                    <label className="toggle">
                      <input type="checkbox" checked={autoRepair} onChange={(event) => setAutoRepair(event.target.checked)} />
                      Auto repair
                    </label>
                  </div>
                </div>
              </div>
            ) : null}

            <div className="composer-row">
              <input
                value={task}
                onChange={(event) => setTask(event.target.value)}
                placeholder={activeProject ? "Ask Jules to build something..." : "Open a project folder to begin"}
                disabled={!activeProject}
              />
              <button className={`run-button ${busy ? "loading" : ""}`} onClick={handleRun} disabled={busy || !task.trim() || !activeProject}>Run</button>
            </div>
            {!activeProject ? <div className="composer-helper">Open a project folder before creating a new session.</div> : null}
          </div>
        </section>
      </main>

      <InspectModal open={projectModalOpen} title="Open Project Folder" onClose={() => setProjectModalOpen(false)}>
        <div className="project-picker-panel">
          <div className="project-picker-intro">
            <strong>Choose a workspace root</strong>
            <p>
              Native folder picking is not available in this browser flow, so Jules uses known workspaces first and a
              manual absolute-path fallback when needed.
            </p>
          </div>

          <section className="project-picker-section">
            <div className="project-picker-heading">
              <strong>Recent projects</strong>
              <span>{recentProjects.length > 0 ? "Reopen or switch instantly" : "No known project roots yet"}</span>
            </div>
            {recentProjects.length > 0 ? (
              <div className="project-picker-list">
                {recentProjects.map((project) => (
                  <button
                    key={project.id}
                    className={project.id === activeProjectId ? "project-picker-row active" : "project-picker-row"}
                    onClick={() => void handleQuickOpenProject(project)}
                    disabled={busy}
                  >
                    <div>
                      <strong>{project.label}</strong>
                      <span>{project.root}</span>
                    </div>
                    <span className="project-picker-meta">
                      {project.runs.length > 0 ? `${pluralize(project.runs.length, "session")} · ${relativeTime(project.runs[0]?.updated_at ?? project.runs[0]?.created_at)}` : "No sessions yet"}
                    </span>
                  </button>
                ))}
              </div>
            ) : (
              <div className="project-picker-empty">
                Open a folder once and it will appear here for quick switching next time.
              </div>
            )}
          </section>

          <section className="project-picker-section">
            <button
              className="advanced-toggle-button"
              onClick={() => setLayout((current) => ({ ...current, projectPickerManualOpen: !current.projectPickerManualOpen }))}
            >
              {layout.projectPickerManualOpen ? "Hide manual path entry" : "Enter a folder path manually"}
            </button>
            {layout.projectPickerManualOpen ? (
              <div className="project-picker-manual">
                <div className="advanced-grid">
                  <label>
                    Folder path
                    <input value={projectPath} onChange={(event) => setProjectPath(event.target.value)} placeholder="C:\\code\\my-project" />
                  </label>
                  <label>
                    Project name
                    <input value={projectName} onChange={(event) => setProjectName(event.target.value)} placeholder="Optional display name" />
                  </label>
                </div>
                <div className="project-picker-actions">
                  <span className="detail-note">Jules will create the project if the root is new, or reopen it if it already exists.</span>
                  <button className="primary-button" onClick={handleCreateProject} disabled={busy || !projectPath.trim()}>
                    Open Folder
                  </button>
                </div>
              </div>
            ) : null}
          </section>

          {error ? (
            <div className="project-picker-error">
              <strong>Could not open that folder</strong>
              <p>{error}</p>
            </div>
          ) : null}
        </div>
      </InspectModal>

      <InspectModal open={aiSettingsOpen} title="AI Settings" onClose={() => setAiSettingsOpen(false)}>
        <div className="ai-settings-panel">
          <div className="advanced-grid">
            <label>
              Provider
              <select value={llmSettings.provider} onChange={(event) => setLlmSettings((current) => ({ ...current, provider: event.target.value }))}>
                <option value="ollama">ollama</option>
              </select>
            </label>
            <label>
              Model
              <input value={llmSettings.model} onChange={(event) => setLlmSettings((current) => ({ ...current, model: event.target.value }))} />
            </label>
            <label>
              Base URL
              <input value={llmSettings.base_url} onChange={(event) => setLlmSettings((current) => ({ ...current, base_url: event.target.value }))} />
            </label>
            <label>
              Retry limit
              <input
                type="number"
                min={0}
                max={4}
                value={llmSettings.retry_limit}
                onChange={(event) => setLlmSettings((current) => ({ ...current, retry_limit: Number(event.target.value) || 0 }))}
              />
            </label>
          </div>
          <div className="toggle-stack">
            <label className="toggle">
              <input type="checkbox" checked={llmSettings.enabled} onChange={(event) => setLlmSettings((current) => ({ ...current, enabled: event.target.checked }))} />
              Enable local planning
            </label>
            <label className="toggle">
              <input type="checkbox" checked={llmSettings.review_enabled} onChange={(event) => setLlmSettings((current) => ({ ...current, review_enabled: event.target.checked }))} />
              Enable plan review
            </label>
            <label className="toggle">
              <input type="checkbox" checked={llmSettings.compression_enabled} onChange={(event) => setLlmSettings((current) => ({ ...current, compression_enabled: event.target.checked }))} />
              Enable context compression
            </label>
          </div>
          <div className="advanced-grid">
            <label>
              Subtasks before compression
              <input
                type="number"
                min={1}
                max={20}
                value={llmSettings.compression_threshold}
                onChange={(event) => setLlmSettings((current) => ({ ...current, compression_threshold: Number(event.target.value) || 5 }))}
              />
            </label>
            <label>
              Embedding Model (Local RAG)
              <input
                type="text"
                value={llmSettings.embedding_model}
                onChange={(event) => setLlmSettings((current) => ({ ...current, embedding_model: event.target.value }))}
                placeholder="nomic-embed-text"
              />
            </label>
          </div>
          <div className="ai-settings-footer">
            <span className="detail-note">Backend-owned settings apply to new runs. Existing sessions keep their recorded provider/model metadata.</span>
            <button className="primary-button" onClick={handleSaveAISettings} disabled={busy}>Save AI Settings</button>
          </div>
        </div>
      </InspectModal>

      <InspectModal open={Boolean(inspectModal)} title={inspectModal?.title ?? ""} onClose={() => setInspectModal(null)}>
        <pre>{prettyJson(inspectModal?.content ?? {})}</pre>
      </InspectModal>
    </div>
  );
}
