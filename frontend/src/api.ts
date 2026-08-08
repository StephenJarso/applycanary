/**
 * Typed client for the FastAPI backend.
 *
 * These interfaces mirror the Pydantic response models in `app/web/api.py`.
 * They are hand-written rather than generated: the surface is small, and a
 * generator would be another build step to keep working. If a shape drifts,
 * the mismatch shows up here first.
 */

export interface Score {
  total: number;
  keyword_score: number;
  semantic_score: number;
  ats_score: number;
  verdict: string;
  reasoning: string;
  decided_by: string;
  disqualifier: string;
  model_used: string;
  matched_keywords: string[];
  missing_keywords: string[];
  scored_at: string | null;
}

export interface Job {
  id: number;
  company: string;
  title: string;
  location: string;
  is_remote: boolean;
  source: string;
  status: string;
  apply_url: string;
  ats_platform: string;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string;
  salary_is_estimate: boolean;
  posted_at: string | null;
  first_seen_at: string;
  seen_count: number;
  age_hours: number;
  score: Score | null;
}

export interface JobDetail extends Job {
  description: string;
  canonical_url: string;
  aliases: Array<{
    source: string;
    matched_by: string;
    match_score: number | null;
    apply_url: string;
  }>;
  application: {
    method: string;
    queued_at: string;
    submitted_at: string | null;
    confirmation: string;
    error: string;
    attempts: number;
    cover_letter: string;
    form_answers: Record<string, string>;
    outcome: string;
  } | null;
  resume_version: {
    text: string;
    diff_summary: string;
    ats_score_before: number;
    ats_score_after: number;
    keywords_added: string[];
    truthcheck_passed: boolean;
    truthcheck_notes: string[];
    unverifiable_claims: string[];
    docx_path: string;
    pdf_path: string;
  } | null;
  interview_prep: {
    technical_questions: Array<{ question: string; answer: string; why: string }>;
    behavioural_questions: Array<{ question: string; answer: string; why: string }>;
    questions_to_ask: string[];
    company_notes: string;
    skill_gaps: string[];
  } | null;
}

export interface JobList {
  jobs: Job[];
  total: number;
  counts: Record<string, number>;
  sources: string[];
}

export interface Profile {
  full_name: string;
  email: string;
  phone: string;
  location: string;
  linkedin_url: string;
  github_username: string;
  portfolio_url: string;
  min_salary: number | null;
  salary_currency: string;
  target_titles: string[];
  target_locations: string[];
  excluded_companies: string[];
  work_authorization: string;
  years_experience: number | null;
  remote_only: boolean;
  has_resume: boolean;
  resume_words: number;
  skills: string[];
  github_synced_at: string | null;
  github_repo_count: number;
}

export interface SourceHealth {
  source: string;
  ok: boolean;
  runs: number;
  failures: number;
  found: number;
  new_jobs: number;
  last_run_at: string | null;
  last_duration_ms: number;
  last_error: string;
}

export interface Status {
  ok: boolean;
  counts: Record<string, number>;
  scheduler_running: boolean;
  scheduled_jobs: string[];
  llm_enabled: boolean;
  auto_submit: boolean;
  auto_submit_min_score: number;
  daily_apply_cap: number;
  warnings: string[];
  has_profile: boolean;
}

export interface ActionResult {
  ok: boolean;
  message: string;
  detail: Record<string, unknown>;
}

export interface AtsFinding {
  rule: string;
  severity: "critical" | "warning" | "info";
  message: string;
  fix: string;
  detail: string;
}

export interface AtsReport {
  score: number;
  passed: boolean;
  findings: AtsFinding[];
}

export interface JobFilters {
  q?: string;
  status?: string;
  source?: string;
  min_score?: number;
  remote_only?: boolean;
  sort?: "score" | "newest" | "oldest";
}

/** Raised for non-2xx responses, carrying the server's `detail` when present. */
export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, {
    headers: init?.body instanceof FormData ? {} : { "Content-Type": "application/json" },
    ...init,
  });

  if (!res.ok) {
    // FastAPI puts the useful message in `detail`; fall back to the status text
    // so the UI never shows a bare "Error".
    let message = res.statusText;
    try {
      const body = (await res.json()) as { detail?: unknown };
      if (typeof body.detail === "string") message = body.detail;
    } catch {
      /* non-JSON error body */
    }
    throw new ApiError(message, res.status);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

const qs = (filters: JobFilters): string => {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value !== undefined && value !== "" && value !== false && value !== 0) {
      params.set(key, String(value));
    }
  }
  const s = params.toString();
  return s ? `?${s}` : "";
};

export const api = {
  status: () => request<Status>("/status"),
  jobs: (filters: JobFilters = {}) => request<JobList>(`/jobs${qs(filters)}`),
  job: (id: number) => request<JobDetail>(`/jobs/${id}`),
  review: () => request<Job[]>("/review"),
  applications: () => request<Job[]>("/applications"),
  sources: () => request<SourceHealth[]>("/sources"),
  profile: () => request<Profile>("/profile"),
  atsReport: () => request<AtsReport>("/profile/ats"),

  saveProfile: (body: Partial<Profile>) =>
    request<Profile>("/profile", { method: "PUT", body: JSON.stringify(body) }),

  uploadResume: (file: File) => {
    const form = new FormData();
    form.append("resume", file);
    return request<ActionResult>("/profile/resume", { method: "POST", body: form });
  },

  tailor: (id: number) => request<ActionResult>(`/jobs/${id}/tailor`, { method: "POST" }),
  submit: (id: number, force = false) =>
    request<ActionResult>(`/jobs/${id}/submit?force=${force}`, { method: "POST" }),
  skip: (id: number) => request<ActionResult>(`/jobs/${id}/skip`, { method: "POST" }),
  prep: (id: number) => request<ActionResult>(`/jobs/${id}/prep`, { method: "POST" }),

  poll: () => request<ActionResult>("/actions/poll", { method: "POST" }),
  score: () => request<ActionResult>("/actions/score", { method: "POST" }),
  syncGithub: () => request<ActionResult>("/actions/github", { method: "POST" }),
};
