export type Status = "new" | "scored" | "tailored" | "applied" | "skipped" | "dismissed" | "error";

export interface JobSummary {
  id: string;
  source: string;
  company: string;
  title: string;
  location: string | null;
  status: Status;
  score: number | null;
  prefit: number | null; // quick fit estimate (no Claude), for jobs not scored yet
  url: string;
  apply_url: string | null;
  posted_at: string | null;
  discovered_at: string;
  applied_at: string | null;
  has_resume: boolean;
}

export interface JobDetail extends JobSummary {
  description: string | null;
  error: string | null;
  match: { score: number; verdict: string; reasons: string[]; missing_requirements: string[] } | null;
  changes: string[];
  cover_letter: string | null;
  resume_file: string | null;
}

export interface Stats extends Record<Status, number> {
  total: number;
  has_resume: boolean;
}

export interface Task {
  label: string;
  args: string[];
  pid: number;
  running: boolean;
  started: string;
  ended: string | null;
}

export interface Config {
  search: {
    keywords: string[];
    locations: string[];
    title_exclude: string[];
    max_age_days: number;
    min_match_score: number;
    [k: string]: unknown;
  };
  sources: {
    linkedin: { enabled: boolean; pages: number };
    naukri: { enabled: boolean; pages: number };
    companies: Partial<Record<"portals" | "greenhouse" | "lever" | "ashby" | "workday" | "smartrecruiters" | "workable", string[]>>;
  };
  applicant: Record<string, string>;
  llm: {
    backend?: string;
    model?: string;
    groq_model?: string;
    score_effort: string;
    tailor_effort: string;
    form_effort: string;
    [k: string]: unknown;
  };
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body.detail) msg = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* not JSON */
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

const post = <T,>(path: string, body?: unknown) =>
  request<T>(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });

export const api = {
  stats: () => request<Stats>("/api/stats"),
  jobs: () => request<JobSummary[]>("/api/jobs"),
  job: (id: string) => request<JobDetail>(`/api/jobs/${id}`),
  setStatus: (id: string, status: Status) => post(`/api/jobs/${id}/status`, { status }),
  scoreOne: (id: string) => post<Task>(`/api/jobs/${id}/score`),
  tailorOne: (id: string) => post<Task>(`/api/jobs/${id}/tailor`),
  applyOne: (id: string, tailor = false) => post<Task>(`/api/jobs/${id}/apply`, { tailor }),
  applyCommand: (command: "fill" | "close") => post("/api/apply/command", { command }),
  openFolder: (id: string) => post(`/api/jobs/${id}/open-folder`),
  search: (body: {
    keywords: string[];
    locations: string[];
    sources: string[];
    max_age_days: number;
    score_limit: number;
    tailor_limit: number;
  }) => post<Task>("/api/search", body),
  score: (limit: number) => post<Task>("/api/score", { limit }),
  tailor: (limit: number) => post<Task>("/api/tailor", { limit }),
  task: () => request<{ task: Task | null; log: string }>("/api/task"),
  stopTask: () => post("/api/task/stop"),
  config: () => request<Config>("/api/config"),
  saveConfig: (patch: Partial<Config>) =>
    request("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  resume: () =>
    request<{ resume: Record<string, any> | null; has_preview?: boolean; has_original?: boolean }>("/api/resume"),
  uploadResume: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<Task>("/api/resume/upload", { method: "POST", body: fd });
  },
  openResumeJson: () => post("/api/resume/open"),
};

export const STATUS_META: Record<Status, { label: string; cls: string }> = {
  new: { label: "Not scored", cls: "bg-slate-100 text-slate-600" },
  scored: { label: "Good match", cls: "bg-blue-100 text-blue-700" },
  tailored: { label: "Resume ready", cls: "bg-indigo-100 text-indigo-700" },
  applied: { label: "Applied", cls: "bg-emerald-100 text-emerald-700" },
  skipped: { label: "Low match", cls: "bg-slate-100 text-slate-500" },
  dismissed: { label: "Dismissed", cls: "bg-slate-100 text-slate-400" },
  error: { label: "Error", cls: "bg-red-100 text-red-700" },
};
