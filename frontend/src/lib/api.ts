/** Typed client for the ctt backend.
 *
 * Field metadata (type, docs, choices) comes from the backend, which reflects
 * it off the Python dataclasses. The settings form is generated from that, so
 * adding a config field needs no frontend change at all.
 */

export type FieldType = "str" | "int" | "float" | "bool" | "list";

export interface ConfigField {
  path: string;
  name: string;
  section: string;
  type: FieldType;
  value: string | number | boolean | string[];
  doc: string;
  choices: string[] | null;
}

export interface ConfigResponse {
  source: string | null;
  default_path: string;
  fields: ConfigField[];
  glossary: Record<string, string>;
  warnings: string[];
}

export type JobStatus = "pending" | "running" | "done" | "failed" | "cancelled";

export interface JobPageResult {
  index: number;
  name: string;
  source_path: string;
  output_path: string;
  bubbles: number;
  review_count: number;
  seconds: number;
}

export interface Job {
  id: string;
  status: JobStatus;
  total: number;
  completed: number;
  page_index: number;
  page_name: string;
  stage: string;
  stages: string[];
  elapsed: number;
  eta: number | null;
  error: string;
  output_dir: string;
  project_path: string;
  log: string[];
  results: JobPageResult[];
}

export interface JobRequest {
  input_dir: string;
  output_dir: string;
  limit?: number;
  recursive?: boolean;
  overrides?: Record<string, unknown>;
}

export interface Candidate {
  path: string;
  name: string;
  parent: string;
  width: number;
  height: number;
  size: number;
  reason: string;
}

export interface PreviewResponse {
  summary: {
    total: number;
    included: number;
    skipped: number;
    reasons: Record<string, number>;
    folders: { path: string; count: number }[];
    estimated_seconds: number;
  };
  included: Candidate[];
  skipped: Candidate[];
}

export interface BrowseEntry {
  name: string;
  path: string;
  images: number;
  nested_images: number;
}

export interface BrowseResponse {
  path: string;
  parent: string | null;
  images: number;
  nested_images: number;
  entries: BrowseEntry[];
}

export interface PreviewCandidate {
  path: string;
  name: string;
  parent: string;
  width: number;
  height: number;
  size: number;
  reason: string;
}

export interface PreviewResponse {
  summary: {
    total: number;
    included: number;
    skipped: number;
    reasons: Record<string, number>;
    folders: { path: string; count: number }[];
    estimated_seconds: number;
  };
  included: PreviewCandidate[];
  skipped: PreviewCandidate[];
}

export interface Block {
  id: string;
  kind: string;
  box: { x1: number; y1: number; x2: number; y2: number };
  source_text: string;
  source_conf: number;
  target_text: string;
  edited: boolean;
  style: { font: string; size: number; align: string; line_spacing: number };
}

export interface Page {
  image_path: string;
  width: number;
  height: number;
  blocks: Block[];
}

export interface Project {
  name: string;
  target_lang: string;
  glossary: Record<string, string>;
  pages: Page[];
}

class ApiError extends Error {
  // Declared and assigned explicitly rather than as a constructor parameter
  // property: the Vite template enables `erasableSyntaxOnly`, which rejects
  // TypeScript syntax that has runtime behaviour.
  status: number;

  constructor(message: string, status: number) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    // FastAPI puts the useful message in `detail`; surfacing the raw status
    // alone ("400 Bad Request") tells the user nothing actionable.
    let detail = `${response.status} ${response.statusText}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* response had no JSON body */
    }
    throw new ApiError(detail, response.status);
  }
  return response.json() as Promise<T>;
}

export const api = {
  health: () => request<{ ok: boolean; pages: number }>("/api/health"),

  getConfig: () => request<ConfigResponse>("/api/config"),
  putConfig: (fields: Record<string, unknown>, glossary?: Record<string, string>) =>
    request<{ saved: string; fields: ConfigField[] }>("/api/config", {
      method: "PUT",
      body: JSON.stringify({ fields, glossary }),
    }),

  browse: (path?: string) =>
    request<BrowseResponse>(`/api/browse${path ? `?path=${encodeURIComponent(path)}` : ""}`),

  /** Opens the OS folder dialog on the machine running the backend.
   *  Legitimate only because that machine is this machine. */
  pickFolder: (initial?: string) =>
    request<{ path: string; cancelled: boolean }>("/api/pick-folder", {
      method: "POST",
      body: JSON.stringify({ initial: initial ?? "" }),
    }),

  createJob: (body: JobRequest) =>
    request<Job>("/api/jobs", { method: "POST", body: JSON.stringify(body) }),
  previewJob: (body: JobRequest) =>
    request<PreviewResponse>("/api/jobs/preview", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  getJob: (id: string) => request<Job>(`/api/jobs/${id}`),
  listJobs: () => request<Job[]>("/api/jobs"),
  cancelJob: (id: string) => request<{ cancelled: string }>(`/api/jobs/${id}/cancel`, { method: "POST" }),

  openProject: (path: string) =>
    request<Project>("/api/project/open", { method: "POST", body: JSON.stringify({ path }) }),
  getProject: () => request<Project>("/api/project"),
  saveProject: () => request<{ saved: string }>("/api/project/save", { method: "POST" }),
  updateBlock: (pageIndex: number, blockId: string, update: Record<string, unknown>) =>
    request<Block>(`/api/pages/${pageIndex}/blocks/${blockId}`, {
      method: "PATCH",
      body: JSON.stringify(update),
    }),

  /** Cache-busted so a re-render after an edit is actually fetched. */
  renderUrl: (pageIndex: number, original = false, nonce = 0) =>
    `/api/pages/${pageIndex}/render?original=${original}&t=${nonce}`,
};

export { ApiError };
