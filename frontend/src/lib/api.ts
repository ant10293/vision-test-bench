import type { Bundle, Dashboard, ProviderInfo, RunSummary, VideoInfo } from "./types";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: {
      ...(init?.body instanceof FormData ? {} : { "Content-Type": "application/json" }),
      ...(init?.headers ?? {}),
    },
  });
  if (!response.ok) {
    let detail = response.statusText;
    try {
      const payload = (await response.json()) as { detail?: string };
      detail = payload.detail ?? detail;
    } catch {
      detail = await response.text();
    }
    throw new Error(detail || `request failed (${response.status})`);
  }
  if (response.status === 204) return undefined as T;
  const contentType = response.headers.get("content-type") ?? "";
  if (contentType.includes("application/json")) return (await response.json()) as T;
  return (await response.blob()) as T;
}

export const api = {
  dashboard: () => request<Dashboard>("/api/dashboard"),
  projects: () => request<{ id: string; name: string; description: string }[]>("/api/projects"),
  createProject: (name: string, description = "") =>
    request<{ id: string }>("/api/projects", { method: "POST", body: JSON.stringify({ name, description }) }),
  videos: (projectId?: string) => request<VideoInfo[]>(`/api/videos${projectId ? `?project_id=${projectId}` : ""}`),
  video: (id: string) => request<VideoInfo>(`/api/videos/${id}`),
  upload: (projectId: string, file: File) => {
    const body = new FormData();
    body.set("project_id", projectId);
    body.set("file", file);
    return request<VideoInfo>("/api/videos", { method: "POST", body });
  },
  deleteVideo: (id: string) => request(`/api/videos/${id}`, { method: "DELETE" }),
  runs: (params: { status?: string; provider_id?: string } = {}) => {
    const query = new URLSearchParams();
    if (params.status) query.set("status", params.status);
    if (params.provider_id) query.set("provider_id", params.provider_id);
    const suffix = query.toString() ? `?${query}` : "";
    return request<RunSummary[]>(`/api/runs${suffix}`);
  },
  run: (id: string) => request<Bundle>(`/api/runs/${id}`),
  createRun: (payload: Record<string, unknown>) =>
    request<RunSummary>("/api/runs", { method: "POST", body: JSON.stringify(payload) }),
  duplicateRun: (id: string) => request<RunSummary>(`/api/runs/${id}/duplicate`, { method: "POST", body: "{}" }),
  cancelRun: (id: string) => request<RunSummary>(`/api/runs/${id}/cancel`, { method: "POST" }),
  deleteRun: (id: string) => request(`/api/runs/${id}`, { method: "DELETE" }),
  retryStage: (id: string, stage: string) => request(`/api/runs/${id}/retry/${stage}`, { method: "POST" }),
  requests: (id: string) => request<Record<string, unknown>[]>(`/api/runs/${id}/requests`),
  scene: (id: string, t: number) => request<Record<string, unknown>>(`/api/runs/${id}/scene?t=${t}`),
  patchObject: (id: string, localId: string, payload: Record<string, unknown>) =>
    request<Bundle>(`/api/runs/${id}/objects/${localId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  addObject: (id: string, label: string, description: string) =>
    request<Bundle>(`/api/runs/${id}/objects`, { method: "POST", body: JSON.stringify({ label, description }) }),
  mergeObjects: (id: string, keepId: string, dropId: string) =>
    request<Bundle>(`/api/runs/${id}/objects/merge`, {
      method: "POST",
      body: JSON.stringify({ keep_id: keepId, drop_id: dropId }),
    }),
  splitObject: (id: string, localId: string, timestamps: number[], label: string) =>
    request<Bundle>(`/api/runs/${id}/objects/${localId}/split`, {
      method: "POST",
      body: JSON.stringify({ timestamps, label }),
    }),
  patchAction: (id: string, localId: string, payload: Record<string, unknown>) =>
    request<Bundle>(`/api/runs/${id}/actions/${localId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  patchStep: (id: string, localId: string, payload: Record<string, unknown>) =>
    request<Bundle>(`/api/runs/${id}/steps/${localId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  addDependency: (id: string, payload: Record<string, unknown>) =>
    request<Bundle>(`/api/runs/${id}/dependencies`, { method: "POST", body: JSON.stringify(payload) }),
  removeDependency: (id: string, fromStep: string, toStep: string) =>
    request<Bundle>(`/api/runs/${id}/dependencies?from_step=${fromStep}&to_step=${toStep}`, { method: "DELETE" }),
  providers: () => request<ProviderInfo[]>("/api/providers"),
  saveKey: (providerId: string, apiKey: string, baseUrl = "") =>
    request(`/api/providers/${providerId}/credentials`, {
      method: "POST",
      body: JSON.stringify({ api_key: apiKey, base_url: baseUrl }),
    }),
  validateProvider: (providerId: string) =>
    request<{ ok: boolean }>(`/api/providers/${providerId}/validate`, { method: "POST" }),
  discoverModels: (providerId: string) =>
    request<{ catalog: ProviderInfo["models"]; discovered: string[]; error: string }>(
      `/api/providers/${providerId}/models`,
    ),
  addModel: (providerId: string, modelId: string) =>
    request(`/api/providers/${providerId}/models`, { method: "POST", body: JSON.stringify({ model_id: modelId }) }),
  prompts: () =>
    request<{ id: string; key: string; name: string; version: number; body: string }[]>("/api/prompts"),
  savePrompt: (key: string, name: string, body: string) =>
    request("/api/prompts", { method: "POST", body: JSON.stringify({ key, name, body }) }),
  promoteTruth: (runId: string, name: string) =>
    request<{ id: string }>(`/api/runs/${runId}/ground-truth`, { method: "POST", body: JSON.stringify({ name }) }),
  groundTruth: (runId: string) => request<{ id: string; name: string }[]>(`/api/runs/${runId}/ground-truth`),
  evaluate: (runId: string, groundTruthId: string, tolerances: Record<string, number>) =>
    request<{ id: string; metrics: Record<string, unknown> }>(`/api/runs/${runId}/evaluations`, {
      method: "POST",
      body: JSON.stringify({ ground_truth_id: groundTruthId, tolerances }),
    }),
  compare: (left: string, right: string) =>
    request<{ id: string; report: Record<string, unknown> }>("/api/comparisons", {
      method: "POST",
      body: JSON.stringify({ left_run_id: left, right_run_id: right }),
    }),
  setMatches: (comparisonId: string, objectMatches: { left: string; right: string }[]) =>
    request<{ report: Record<string, unknown> }>(`/api/comparisons/${comparisonId}/matches`, {
      method: "POST",
      body: JSON.stringify({ object_matches: objectMatches }),
    }),
  tasks: () => request<{ id: string; name: string; description: string; created_at: string }[]>("/api/tasks"),
  task: (id: string) => request<TaskBundle>(`/api/tasks/${id}`),
  createTask: (name: string, description: string) =>
    request<TaskBundle>("/api/tasks", { method: "POST", body: JSON.stringify({ name, description }) }),
  uploadClips: (projectId: string, files: File[], researchMode: string) => {
    const body = new FormData();
    body.set("research_mode", researchMode);
    for (const file of files) body.append("files", file);
    return request<TaskBundle>(`/api/tasks/${projectId}/clips`, { method: "POST", body });
  },
  createSession: (projectId: string, name: string) =>
    request<TaskBundle>(`/api/tasks/${projectId}/sessions`, { method: "POST", body: JSON.stringify({ name }) }),
  patchSession: (projectId: string, sessionId: string, payload: Record<string, unknown>) =>
    request<TaskBundle>(`/api/tasks/${projectId}/sessions/${sessionId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  splitSession: (projectId: string, sessionId: string, videoIds: string[], name: string) =>
    request<TaskBundle>(`/api/tasks/${projectId}/sessions/${sessionId}/split`, {
      method: "POST",
      body: JSON.stringify({ video_ids: videoIds, name }),
    }),
  orderClips: (projectId: string, videoIds: string[]) =>
    request<TaskBundle>(`/api/tasks/${projectId}/order`, { method: "POST", body: JSON.stringify({ video_ids: videoIds }) }),
  patchClip: (projectId: string, videoId: string, payload: Record<string, unknown>) =>
    request<TaskBundle>(`/api/tasks/${projectId}/clips/${videoId}`, { method: "PATCH", body: JSON.stringify(payload) }),
  reconcileTask: (projectId: string) => request<TaskBundle>(`/api/tasks/${projectId}/reconcile`, { method: "POST" }),
  confirmLink: (projectId: string, linkId: string) =>
    request<TaskBundle>(`/api/tasks/${projectId}/identity/confirm`, { method: "POST", body: JSON.stringify({ link_id: linkId }) }),
  rejectLink: (projectId: string, linkId: string) =>
    request<TaskBundle>(`/api/tasks/${projectId}/identity/reject`, { method: "POST", body: JSON.stringify({ link_id: linkId }) }),
  mergeIdentity: (projectId: string, keepId: string, dropId: string) =>
    request<TaskBundle>(`/api/tasks/${projectId}/identity/merge`, {
      method: "POST",
      body: JSON.stringify({ keep_id: keepId, drop_id: dropId }),
    }),
  splitIdentity: (projectId: string, linkId: string) =>
    request<TaskBundle>(`/api/tasks/${projectId}/identity/split`, { method: "POST", body: JSON.stringify({ link_id: linkId }) }),
  patchTransition: (projectId: string, transitionId: string, payload: Record<string, unknown>) =>
    request<TaskBundle>(`/api/tasks/${projectId}/transitions/${transitionId}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
};

export interface TaskBundle {
  project: { id: string; name: string; description: string; created_at: string };
  sessions: { id: string; name: string; ordinal: number; locked: boolean; notes: string; video_ids: string[] }[];
  clips: TaskClip[];
  order: { method?: string; fact: boolean; ranked: string[][]; unordered: string[]; ambiguous_ids: string[]; notes: string[] };
  context_hypothesis: { fact: boolean; pairs: string[][]; evidence: { support?: string }[]; note?: string };
  canonical_objects: {
    id: string;
    local_id: string;
    label: string;
    description: string;
    color: string;
    uncertain: boolean;
    history: {
      video_id: string;
      object_local_id: string;
      label: string;
      state_before: string;
      state_after: string;
      image_url: string;
      status: string;
      uncertain: boolean;
    }[];
  }[];
  links: {
    id: string;
    canonical_id: string;
    video_id: string;
    object_local_id: string;
    status: string;
    uncertain: boolean;
    note: string;
    score: number;
  }[];
  transitions: {
    id: string;
    from_video_id: string;
    to_video_id: string;
    observed_change: string;
    hypothesis: string;
    evidence_class: string;
    verified: boolean;
    gap_note: string;
  }[];
  workflow: {
    steps: { id: string; kind: string; label: string; video_ids?: string[]; evidence_class?: string; note?: string; observed_change?: string; hypothesis?: string }[];
    procedural_dependencies: { before: string; after: string; evidence_class: string; accepted: boolean; note: string }[];
    continuous_sequence: boolean;
  };
  revision: { id: string; version: number; change_summary: string; document: Record<string, unknown> } | null;
  revisions: { id: string; version: number; change_summary: string; trigger: string; document: Record<string, unknown> }[];
  time_basis: { project_time: string; clip_time: string };
  jobs_created?: number;
}

export interface TaskClip {
  id: string;
  filename: string;
  duration_sec: number;
  session_id: string | null;
  research_mode: string;
  capture_time_raw: string;
  capture_time_utc: string;
  timestamp_source: string;
  timestamp_reliability: string;
  timestamp_note: string;
  file_mtime: string;
  uploaded_at: string;
  order_locked: boolean;
  order_ambiguous: boolean;
  order_method: string;
  playback_url: string;
  thumbnail_url: string;
  run_id: string;
  actions: { local_id: string; action_type: string; description: string; start: number; end: number; evidence_class: string }[];
}

export function formatTime(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "—";
  const sign = seconds < 0 ? "-" : "";
  const abs = Math.abs(seconds);
  const mins = Math.floor(abs / 60);
  const rest = abs - mins * 60;
  return `${sign}${mins}:${rest.toFixed(2).padStart(5, "0")}`;
}

export function evidenceClass(value: string | undefined): string {
  if (value === "DIRECTLY_OBSERVED") return "observed";
  if (value === "INFERRED") return "inferred";
  return "unknown";
}
