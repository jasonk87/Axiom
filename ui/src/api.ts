import type { ArtifactContent, AxiomProject, AxiomSession, FileTreeNode, LLMSettings, PrepareRunRequest } from "./types";

async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
      ...(options?.headers ?? {}),
    },
    ...options,
  });

  if (!response.ok) {
    const errorText = await response.text();
    try {
      const parsed = JSON.parse(errorText) as { error?: string };
      throw new Error(parsed.error || `Request failed with status ${response.status}`);
    } catch {
      throw new Error(errorText || `Request failed with status ${response.status}`);
    }
  }

  return response.json() as Promise<T>;
}

export async function fetchProjectTree(projectPath: string): Promise<{ projectPath: string; tree: FileTreeNode[] }> {
  return request("/api/project/tree", {
    method: "POST",
    body: JSON.stringify({ projectPath }),
  });
}

export async function fetchProjects(): Promise<{ projects: AxiomProject[]; active_project_id?: string | null; active_session_id?: string | null }> {
  return request("/api/projects");
}

export async function fetchProject(projectId: string): Promise<AxiomProject & { sessions?: AxiomSession[] }> {
  return request(`/api/projects/${projectId}`);
}

export async function createProject(path: string, name?: string): Promise<AxiomProject> {
  return request("/api/projects", {
    method: "POST",
    body: JSON.stringify({ path, name }),
  });
}

export async function setActiveProject(projectId: string): Promise<{ projects: AxiomProject[]; active_project_id?: string | null; active_session_id?: string | null }> {
  return request("/api/projects/active", {
    method: "POST",
    body: JSON.stringify({ projectId }),
  });
}

export async function fetchRuns(): Promise<{ runs: AxiomSession[] }> {
  return request("/api/runs");
}

export async function fetchLLMSettings(): Promise<LLMSettings> {
  return request("/api/llm/settings");
}

export async function updateLLMSettings(payload: Partial<LLMSettings>): Promise<LLMSettings> {
  return request("/api/llm/settings", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function prepareRun(payload: PrepareRunRequest): Promise<AxiomSession> {
  return request("/api/runs/prepare", {
    method: "POST",
    body: JSON.stringify(payload),
  });
}

export async function fetchRun(sessionId: string): Promise<AxiomSession> {
  return request(`/api/runs/${sessionId}`);
}

export async function approvePlan(sessionId: string): Promise<AxiomSession> {
  return request(`/api/runs/${sessionId}/approve-plan`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function approvePhase(sessionId: string): Promise<AxiomSession> {
  return request(`/api/runs/${sessionId}/approve-phase`, {
    method: "POST",
    body: JSON.stringify({}),
  });
}

export async function declinePlan(sessionId: string, reason?: string): Promise<AxiomSession> {
  return request(`/api/runs/${sessionId}/decline-plan`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export async function declinePhase(sessionId: string, reason?: string): Promise<AxiomSession> {
  return request(`/api/runs/${sessionId}/decline-phase`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export async function cancelRun(sessionId: string, reason?: string): Promise<AxiomSession> {
  return request(`/api/runs/${sessionId}/cancel`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export async function fetchArtifact(path: string): Promise<ArtifactContent> {
  const url = `/api/artifact?path=${encodeURIComponent(path)}`;
  return request(url);
}
