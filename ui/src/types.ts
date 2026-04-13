export type FileTreeNode = {
  name: string;
  path: string;
  type: "file" | "directory";
  children?: FileTreeNode[];
};

export type ArtifactReference = {
  artifact_type: string;
  label: string;
  path: string;
};

export type AxiomProject = {
  id: string;
  name: string;
  root_path: string;
  created_at: string;
  updated_at: string;
  memory?: {
    project_id: string;
    project_summary: string;
    known_commands: string[];
    recent_context_summary: string;
  };
};

export type LLMSettings = {
  enabled: boolean;
  review_enabled: boolean;
  provider: string;
  base_url: string;
  model: string;
  timeout_seconds: number;
  retry_limit: number;
  temperature: number;
  compression_enabled: boolean;
  compression_threshold: number;
  embedding_model: string;
};

export type LLMStructuredSummary = {
  feature: string;
  provider: string;
  model: string;
  enabled: boolean;
  accepted: boolean;
  fallback_used: boolean;
  attempts_used: number;
  retry_limit: number;
  final_message: string;
  accepted_payload?: Record<string, unknown> | null;
  events: Array<{
    stage: string;
    status: string;
    summary: string;
    attempt: number;
    details?: Record<string, unknown>;
    artifact_reference?: ArtifactReference | null;
  }>;
  artifact_references: ArtifactReference[];
};

export type PreviewFileChange = {
  path: string;
  action: string;
  blocked: boolean;
  summary: string;
  before_exists?: boolean;
  before_preview?: string | null;
  after_preview?: string | null;
  diff_preview?: string | null;
  origin?: Record<string, unknown>;
  content_hashes?: Record<string, string | null>;
};

export type AxiomSession = {
  id: string;
  session_id?: string;
  run_id?: string;
  project_id?: string;
  project_name?: string;
  project_root: string;
  status: string;
  mode: string;
  updated_at?: string;
  created_at?: string;
  pending_phase?: string | null;
  activity?: string[];
  effective_scope?: {
    mode: string;
    paths: string[];
    description: string;
  };
  protected_paths?: string[];
  task_interpretation?: {
    summary: string;
    raw_task?: string;
    action: string;
    target_path?: string | null;
    compressed_history?: string | null;
    compressed_subtask_count?: number;
  };
  repo_index_summary?: {
    generated?: boolean;
    total_files?: number;
    likely_entry_files?: string[];
    [key: string]: unknown;
  } | null;
  files_modified?: string[];
  blocked_actions?: Array<{
    path?: string;
    reason?: string;
    [key: string]: unknown;
  }>;
  llm_settings?: LLMSettings | null;
  llm_summary?: LLMStructuredSummary | null;
  llm_review_summary?: LLMStructuredSummary | null;
  plan?: {
    steps: Array<{
      id: string;
      type: string;
      title: string;
      description: string;
      dependencies: string[];
      scope_hint: string;
      expected_outcome: string;
      phase: string;
      risk_hint: string;
      approval_hint: string;
    }>;
  } | null;
  subtasks?: Array<{
    action: string;
    description: string;
    target_path?: string | null;
    command?: string | null;
    result_summary?: string | null;
  }>;
  current_subtask_index?: number;
  current_subtask_content?: string | null;
  current_subtask_command?: string | null;
  phase_policies?: Array<{
    phase: string;
    classification: string;
    approval_required: boolean;
    auto_run_allowed: boolean;
    reason: string;
    auto_ran: boolean;
  }>;
  change_preview?: {
    intended_file_writes: string[];
    intended_commands: string[];
    relevant_steps: string[];
    file_changes: PreviewFileChange[];
    blocked_writes: Array<{ path: string; reason: string }>;
  } | null;
  phase_results?: Array<{
    phase: string;
    status: string;
    message: string;
    classification?: string | null;
    approval_required: boolean;
    auto_ran: boolean;
    approval_reason: string;
    step_ids: string[];
    verification_outcomes: Array<Record<string, unknown>>;
  }>;
  step_results?: Array<{
    step_id: string;
    title: string;
    step_type: string;
    status: string;
    message: string;
    phase: string;
    details: Record<string, unknown>;
  }>;
  artifact_references?: ArtifactReference[];
  commands_run?: Array<{
    command: string;
    exit_code: number;
    stdout: string;
    stderr: string;
    success: boolean;
    cancelled?: boolean;
    summary: string;
    artifact_reference?: ArtifactReference | null;
  }>;
  failure_classification?: Record<string, unknown> | null;
  repair_summary?: Record<string, unknown> | null;
  final_execution_result?: {
    success: boolean;
    message: string;
    details: Record<string, unknown>;
  } | null;
  context_package?: {
    project_memory?: {
      summary: string;
      known_commands: string[];
      recent_context: string;
    } | null;
    [key: string]: unknown;
  } | null;
  result?: Record<string, unknown> | null;
};

export type ArtifactContent = {
  path: string;
  isJson: boolean;
  content: unknown;
};

export type PrepareRunRequest = {
  projectId?: string;
  projectPath?: string;
  mode: string;
  task: string;
  approvalMode: string;
  verificationProfile: string;
  verificationCommands: string[];
  commandPolicy: string;
  previewChanges: boolean;
  buildRepoIndex: boolean;
  autoRepair: boolean;
  scopePaths: string[];
  protectedPaths: string[];
};
