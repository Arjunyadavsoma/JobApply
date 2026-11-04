export type Status =
  | "new" | "scored" | "tailored" | "applied" | "assessment" | "interview" | "offer"
  | "rejected" | "withdrawn" | "expired" | "skipped" | "dismissed" | "error";

export interface JobSummary {
  id: string;
  source: string;
  company: string;
  title: string;
  location: string | null;
  status: Status;
  score: number | null;
  prefit: number | null; // quick fit estimate (no AI), for jobs not scored yet
  url: string;
  apply_url: string | null;
  posted_at: string | null;
  discovered_at: string;
  applied_at: string | null;
  has_resume: boolean;
  // enrichment
  salary_min: number | null;
  salary_max: number | null;
  salary_text: string | null;
  stipend: number | null;
  employment_type: string | null;
  remote_type: string | null;
  experience_min: number | null;
  experience_max: number | null;
  batch_years: number[] | null;
  deadline: string | null;
  fresher_flag: number | null;
  walk_in: number | null;
  off_campus: number | null;
  verified: number | null;
  fraud_flags: string[] | null;
  notes: string | null;
  min_cgpa: number | null;
  min_percentage: number | null;
}

export interface EligibilityCheck {
  criterion: string; required: string; candidate: string; result: "pass" | "fail" | "unknown"; note: string;
}

export interface Eligibility {
  status: "eligible" | "likely_eligible" | "needs_verification" | "not_eligible";
  checks: EligibilityCheck[];
}

export interface JobEvent {
  id: number; job_id: string; kind: string; text: string | null; created_at: string;
}

export interface JobDetail extends JobSummary {
  description: string | null;
  error: string | null;
  match: {
    score: number; verdict: string;
    skills_score?: number; education_score?: number; experience_score?: number; location_score?: number;
    reasons: string[]; missing_skills?: string[]; missing_requirements: string[];
    disqualifiers?: string[]; recommendations?: string[];
    eligibility?: Eligibility;
  } | null;
  eligibility: Eligibility | null;
  changes: string[];
  cover_letter: string | null;
  resume_file: string | null;
  events: JobEvent[];
}

export interface Stats extends Partial<Record<Status, number>> {
  total: number;
  has_resume: boolean;
  has_profile: boolean;
}

export interface Task {
  label: string;
  args: string[];
  pid: number;
  running: boolean;
  started: string;
  ended: string | null;
}

export interface CandidateProfile {
  full_name: string; email: string; phone: string; current_location: string;
  linkedin_url: string; github_url: string; portfolio_url: string;
  graduation_year: number | null; degree: string; branch: string; college: string; university: string;
  cgpa: number | null; cgpa_scale: number; tenth_percentage: number | null; twelfth_percentage: number | null;
  diploma_percentage: number | null; active_backlogs: number; previous_backlogs: number;
  candidate_type: string; total_experience_years: number; work_authorization: string;
  skills: string[]; preferred_roles: string[]; secondary_roles: string[]; preferred_locations: string[];
  open_to_relocation: boolean; open_to_remote: boolean;
  expected_ctc_lpa: number | null; expected_stipend_inr: number | null;
  notice_period: string; current_ctc_lpa: number | null;
  extra: Record<string, string>;
}

export interface Notification {
  id: number; kind: string; title: string; body: string | null;
  job_id: string | null; read: number; created_at: string;
}

export interface Answer { id: number; question: string; answer: string; tags: string | null; updated_at: string; }

export interface Analytics {
  metrics: Record<string, number>;
  applications_over_time: { week: string; applications: number }[];
  status_funnel: Record<string, number>;
  match_distribution: { bucket: string; jobs: number }[];
  source_quality: { source: string; jobs: number; avg_score: number | null; applied: number; in_progress: number; response_rate: number }[];
  missing_skills: { skill: string; jobs: number }[];
  eligibility: { status: string; jobs: number }[];
  upcoming_deadlines: { id: string; company: string; title: string; deadline: string; status: string }[];
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
    [k: string]: unknown;
  };
  applicant: Record<string, string>;
  llm: {
    backend?: string;
    model?: string;
    groq_model?: string;
    fallback_model?: string;
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
  jobs: (params?: Record<string, string | number>) => {
    const q = new URLSearchParams();
    for (const [k, v] of Object.entries(params ?? {})) if (v !== "" && v !== 0) q.set(k, String(v));
    const qs = q.toString();
    return request<JobSummary[]>(`/api/jobs${qs ? `?${qs}` : ""}`);
  },
  job: (id: string) => request<JobDetail>(`/api/jobs/${id}`),
  setStatus: (id: string, status: Status, note = "") => post(`/api/jobs/${id}/status`, { status, note }),
  setNotes: (id: string, notes: string) => post(`/api/jobs/${id}/notes`, { notes }),
  verifyJob: (id: string) => post<{ ok: boolean; status: string }>(`/api/jobs/${id}/verify`),
  scoreOne: (id: string) => post<Task>(`/api/jobs/${id}/score`),
  tailorOne: (id: string) => post<Task>(`/api/jobs/${id}/tailor`),
  applyOne: (id: string, tailor = false) => post<Task>(`/api/jobs/${id}/apply`, { tailor }),
  applyCommand: (command: "fill" | "close") => post("/api/apply/command", { command }),
  openFolder: (id: string) => post(`/api/jobs/${id}/open-folder`),
  askAnswer: (id: string, question: string, maxChars = 1000) =>
    post<{ answer: string; used_saved_answer: boolean; grounded: boolean }>(`/api/jobs/${id}/answer`, {
      question, max_chars: maxChars,
    }),
  aiService: (service: "interview_prep" | "follow_up" | "skill_gap" | "quality_check", jobId: string) =>
    post<Record<string, unknown>>(`/api/ai/${service}?job_id=${encodeURIComponent(jobId)}`),
  search: (body: {
    keywords: string[]; locations: string[]; sources: string[]; max_age_days: number;
    score_limit: number; tailor_limit: number;
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
  analytics: () => request<Analytics>("/api/analytics"),
  notifications: (unread = false) => request<Notification[]>(`/api/notifications${unread ? "?unread=true" : ""}`),
  readNotification: (id: number) => post(`/api/notifications/${id}/read`),
  readAllNotifications: () => post("/api/notifications/read-all"),
  profile: () => request<{ profile: CandidateProfile }>("/api/profile"),
  saveProfile: (patch: Partial<CandidateProfile>) =>
    request<{ ok: boolean; profile: CandidateProfile }>("/api/profile", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(patch),
    }),
  answers: () => request<Answer[]>("/api/answers"),
  saveAnswer: (question: string, answer: string, tags = "") =>
    request("/api/answers", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ question, answer, tags }),
    }),
  deleteAnswer: (id: number) => request(`/api/answers/${id}`, { method: "DELETE" }),
  resume: () =>
    request<{ resume: Record<string, any> | null; has_preview?: boolean; has_original?: boolean }>("/api/resume"),
  uploadResume: (file: File) => {
    const fd = new FormData();
    fd.append("file", file);
    return request<Task>("/api/resume/upload", { method: "POST", body: fd });
  },
  openResumeJson: () => post("/api/resume/open"),

  // ---- Phase 17: queue, agents, verification, external sources, emails, tasks ----
  queue: (states?: string[]) => {
    const qs = states?.length ? `?states=${encodeURIComponent(states.join(","))}` : "";
    return request<{ rows: QueueRow[]; limits: QueueLimits; daily_used: number }>(`/api/queue${qs}`);
  },
  queueAction: (jobId: string, action: "analyze" | "shortlist" | "prepare" | "approve") =>
    post<{ ok?: boolean; error?: string; queue_state?: string }>(`/api/queue/${jobId}/action/${action}`),
  queueApply: (jobId: string, mode: "assisted" | "authorized", tailor = true) =>
    post<{ ok: boolean; mode?: string; error?: string; task?: Task }>(`/api/queue/${jobId}/apply`, { mode, tailor }),
  agents: () => request<{ key: string; name: string; prompt_version: string; description: string }[]>("/api/agents"),
  runAgent: (agent: string, jobId: string, question?: string) =>
    post<Record<string, unknown>>(`/api/agents/${agent}/jobs/${jobId}`, question ? { notes: question } : undefined),
  searchProfiles: () =>
    request<{ profiles: Record<string, unknown>[]; active: Record<string, unknown> | null;
              quick_views: { key: string; label: string; filters: string }[] }>("/api/search-profiles"),
  applySearchProfile: (name: string) => post(`/api/search-profiles/${encodeURIComponent(name)}/apply`),
  externalSources: () =>
    request<{ key: string; name: string; priority: number; reason: string; kind: string;
              search_url: string | null; fresher_url: string | null }[]>("/api/sources/external"),
  atsDetect: (url: string) =>
    request<{ platform: string; name: string; confidence: number; notes: string }>(`/api/ats/detect?url=${encodeURIComponent(url)}`),
  emails: (unreviewed = false) => request<EmailMessage[]>(`/api/emails${unreviewed ? "?unreviewed=true" : ""}`),
  emailStatus: () => request<{ configured: boolean; transport: string | null; note: string }>("/api/emails/status"),
  syncEmails: () => post<{ ok: boolean; new?: number; duplicate?: number; needs_review?: number }>("/api/emails/sync"),
  taskHistory: () => request<{ id: number; label: string; started_at: string; ended_at: string | null }[]>("/api/tasks"),
  backup: () => post<{ ok: boolean; path: string; bytes: number }>("/api/backup"),
};

export interface QueueRow {
  job_id: string;
  company: string;
  title: string;
  salary: string | null;
  stipend: number | null;
  location: string | null;
  remote_type: string | null;
  match_score: number | null;
  quality_score: number | null;
  verification_state: string;
  eligibility: string;
  deadline: string | null;
  deadline_urgency: number;
  resume_ready: boolean;
  queue_state: string;
  pipeline_status: string;
  apply_allowed: boolean;
  apply_block_reason: string;
}

export interface QueueLimits {
  max_per_day: number;
  max_per_company: number;
  min_match_score: number;
  min_eligibility: string;
  min_quality_score: number;
  min_salary_lpa: number;
  preferred_locations: string[];
  authorized_submission: boolean;
  daily_used: number;
}

export interface EmailMessage {
  id: number;
  message_id: string;
  sender: string;
  subject: string;
  snippet: string;
  received_at: string;
  classification: string | null;
  confidence: number;
  matched_job_id: string | null;
  reviewed: number;
}

export const STATUS_META: Record<Status, { label: string; cls: string }> = {
  new: { label: "Not scored", cls: "bg-slate-100 text-slate-600" },
  scored: { label: "Good match", cls: "bg-blue-100 text-blue-700" },
  tailored: { label: "Ready to apply", cls: "bg-indigo-100 text-indigo-700" },
  applied: { label: "Applied", cls: "bg-emerald-100 text-emerald-700" },
  assessment: { label: "Assessment", cls: "bg-amber-100 text-amber-700" },
  interview: { label: "Interview", cls: "bg-violet-100 text-violet-700" },
  offer: { label: "Offer", cls: "bg-green-100 text-green-800" },
  rejected: { label: "Rejected", cls: "bg-red-100 text-red-700" },
  withdrawn: { label: "Withdrawn", cls: "bg-slate-100 text-slate-500" },
  expired: { label: "Expired", cls: "bg-orange-100 text-orange-700" },
  skipped: { label: "Low match", cls: "bg-slate-100 text-slate-500" },
  dismissed: { label: "Dismissed", cls: "bg-slate-100 text-slate-400" },
  error: { label: "Error", cls: "bg-red-100 text-red-700" },
};

export const ELIGIBILITY_META: Record<string, { label: string; cls: string }> = {
  eligible: { label: "Eligible", cls: "bg-emerald-100 text-emerald-700" },
  likely_eligible: { label: "Likely eligible", cls: "bg-lime-100 text-lime-700" },
  needs_verification: { label: "Needs verification", cls: "bg-amber-100 text-amber-700" },
  not_eligible: { label: "Not eligible", cls: "bg-red-100 text-red-700" },
};

export function salaryLabel(j: JobSummary): string {
  if (j.stipend) return `₹${j.stipend.toLocaleString("en-IN")}/mo`;
  if (j.salary_min != null && j.salary_max != null)
    return j.salary_min === j.salary_max ? `₹${j.salary_min} LPA` : `₹${j.salary_min}-${j.salary_max} LPA`;
  return "";
}

export const SOURCE_LABEL: Record<string, string> = {
  greenhouse: "Career site", lever: "Career site", ashby: "Career site", workday: "Career site",
  smartrecruiters: "Career site", workable: "Career site", amazon: "Career site", google: "Career site",
  apple: "Career site", eightfold: "Career site", oracle: "Career site", mynexthire: "Career site",
  portal: "Career site", linkedin: "LinkedIn", naukri: "Naukri", instahyre: "Instahyre",
  remotive: "Remotive", arbeitnow: "Arbeitnow", successfactors: "Career site",
};
