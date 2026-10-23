/**
 * Typed client for the FastAPI backend.
 *
 * These interfaces mirror the Pydantic response models in `app/api/router.py`.
 * They are hand-written rather than generated: the surface is small, and a
 * generator would be another build step to keep working. If a shape drifts,
 * the mismatch shows up here first.
 */

/**
 * Base URL for API calls.
 *
 * - Web build: empty (same-origin, the Vite dev proxy / FastAPI mount answers).
 * - Capacitor app: the WebView origin is capacitor://localhost (iOS) or
 *   http://localhost (Android), so a relative "/api" would hit the shell, not
 *   the backend. Set VITE_API_BASE (e.g. https://canary.example.com) at build
 *   time, or override at runtime with localStorage["ac.api-base"] for dev.
 */
import { Capacitor } from "@capacitor/core";

export const API_BASE: string =
  (typeof localStorage !== "undefined" && localStorage.getItem("ac.api-base")) ||
  (import.meta.env?.VITE_API_BASE as string | undefined) ||
  "";

/** True when running inside the Capacitor native shell (not a plain browser). */
export const IS_NATIVE: boolean = Capacitor.isNativePlatform();

export interface AuthUser {
  id: number;
  email: string;
  is_admin: boolean;
  email_verified: boolean;
  /** False from /register when verification is enforced: account made, no session yet. */
  session_started: boolean;
}

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
  applicants: number | null;
  benefits: string[];
  employment_type: string;
  posted_at: string | null;
  first_seen_at: string;
  seen_count: number;
  age_hours: number;
  score: Score | null;
  apply_email: string;
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
    apply_email: string;
  } | null;
  resume_version: {
    id: number;
    text: string;
    text_html: string;
    original_text: string;
    original_html: string;
    format_preserved: boolean;
    template_note: string;
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
    speech_interview: Array<{
      question: string;
      expected_key_points: string[];
      time_minutes: number;
      evaluation_rubric: Record<string, string>;
    }>;
    technical_interview: Array<{
      question: string;
      starter_code: string;
      expected_solution: string;
      time_minutes: number;
      evaluation_rubric: Record<string, string>;
    }>;
  } | null;
}

export interface JobList {
  jobs: Job[];
  total: number;
  counts: Record<string, number>;
  sources: string[];
}

export interface ReviewJob extends Job {
  /** Address an email application would be sent to; empty for form-only postings. */
  apply_email: string;
}

export interface ReviewList {
  jobs: ReviewJob[];
  total: number;
}

export interface PublicJob {
  id: number;
  company: string;
  title: string;
  location: string;
  is_remote: boolean;
  source: string;
  apply_url: string;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string;
  salary_is_estimate: boolean;
  applicants: number | null;
  benefits: string[];
  employment_type: string;
  posted_at: string | null;
  first_seen_at: string;
}

export interface PublicJobList {
  jobs: PublicJob[];
  total: number;
  sources: string[];
}
export interface EntryLevelRole {
  slug: string;
  title: string;
  employer: string;
  location: string;
  category: string;
  employer_type: string;
  agency: string;
  seniority: string;
  work_mode: string;
  is_remote: boolean;
  salary_disclosed: boolean;
  closing_at: string | null;
  closing_label: string;
  summary: string;
  /** Employer's own advert, extracted by work.ke from the original posting. */
  apply_url: string;
  /** The work.ke listing, kept for provenance. */
  source_url: string;
  employment_type?: string;
  county?: string;
  location_labels?: string[];
}

export interface EntryLevelFeed {
  roles: EntryLevelRole[];
  fetched_at: string;
  source: string;
  warnings: string[];
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
  open_to_relocation: boolean;
  has_resume: boolean;
  resume_words: number;
  base_resume_filename?: string;
  base_resume_text?: string;
  skills: string[];
  github_synced_at: string | null;
  github_repo_count: number;
  github_connected: boolean;
  alert_min_score: number;
  /** Auto-submit preferences */
  enable_auto_submit: boolean;
  auto_submit_min_score: number;
  daily_apply_cap: number;
  llm_provider: string;
  llm_api_key_masked: string;
  preferred_work_type: string;
  /** True once a mailbox + app password are stored; the password is never returned. */
  email_sending_configured: boolean;
  smtp_user: string;
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
  poll_running: boolean;
  poll_message: string;
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
  prep_only?: boolean;
  include_all?: boolean;
  posted_within?: number;
  sort?: "score" | "newest" | "oldest";
  experience_level?: string;
  work_arrangement?: string;
  location?: string;
  employment_type?: string;
  visa_sponsorship?: boolean;
  min_salary?: number;
  limit?: number;
  offset?: number;
}

export interface VoiceConfig {
  tts: "polly" | "browser";
  stt: "transcribe" | "browser";
  voice_id: string;
  aws_enabled: boolean;
}

export interface InterviewQuestion {
  question: string;
  expected_key_points: string[];
  time_minutes: number | null;
  evaluation_rubric: Record<string, string>;
}

export interface InterviewTurn {
  id: number;
  question_index: number;
  question: string;
  expected_key_points: string[];
  time_minutes: number | null;
  rubric: Record<string, string>;
  answer_text: string;
  score: number | null;
  feedback: string;
  strengths: string[];
  improvements: string[];
  model_used: string;
}

export interface InterviewSession {
  id: number;
  job_id: number;
  status: string;
  mode: string;
  question_index: number;
  total_questions: number;
  avg_score: number | null;
  summary: Record<string, unknown>;
  finished: boolean;
  started_at: string | null;
  finished_at: string | null;
}

export interface InterviewState {
  session: InterviewSession;
  current_question: InterviewQuestion | null;
  turn: InterviewTurn | null;
  memory: Array<{ kind: string; content: string; created_at: string | null }>;
}

export interface SimilarJob {
  id: number;
  title: string;
  company: string;
  location: string;
  is_remote: boolean;
  source: string;
  apply_url: string;
  salary_min: number | null;
  salary_max: number | null;
  salary_currency: string;
  salary_is_estimate: boolean;
  applicants: number | null;
  benefits: string[];
  employment_type: string;
  posted_at: string | null;
  first_seen_at: string;
  similarity: number;
}

export interface MemoryEntry {
  id: number;
  kind: string;
  content: string;
  metadata: Record<string, unknown>;
  created_at: string | null;
}

export interface MemorySession {
  id: number;
  job_id: number;
  status: string;
  mode: string;
  avg_score: number | null;
  summary: Record<string, unknown>;
  started_at: string | null;
  finished_at: string | null;
}

export interface MemoryIndex {
  entries: MemoryEntry[];
  sessions: MemorySession[];
  trend: Array<{ score: number; date: string | null }>;
  counts: { sessions: number; memories: number };
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

export function request<T>(path: string, init?: RequestInit, retries?: number): Promise<T>;
export function request<T>(method: string, path: string, body?: unknown): Promise<T>;
export async function request<T>(
  pathOrMethod: string,
  initOrPath?: RequestInit | string,
  bodyOrRetries?: unknown,
): Promise<T> {
  let path: string;
  let init: RequestInit | undefined;
  let retries = 1;

  if (typeof initOrPath === "string") {
    const rawPath = initOrPath;
    path = rawPath.startsWith("/api") ? rawPath.slice(4) : rawPath;
    init = {
      method: pathOrMethod,
      body: bodyOrRetries !== undefined ? JSON.stringify(bodyOrRetries) : undefined,
    };
  } else {
    const rawPath = pathOrMethod;
    path = rawPath.startsWith("/api") ? rawPath.slice(4) : rawPath;
    init = initOrPath;
    if (typeof bodyOrRetries === "number") {
      retries = bodyOrRetries;
    }
  }

  const headers = new Headers(init?.headers);
  if (typeof init?.body === "string" && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  // Cross-origin (native app): cookies must be requested explicitly or the
  // FastAPI session cookie is never stored by the WebView.
  const crossOrigin = API_BASE !== "" && !API_BASE.includes(window.location.host);
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/api${path}`, {
      ...init,
      headers,
      credentials: crossOrigin ? "include" : init?.credentials,
    });
  } catch {
    if (retries > 0) {
      await new Promise((r) => setTimeout(r, 1500));
      return request<T>(path, init, retries - 1);
    }
    // fetch only rejects on network-level problems: offline, DNS failure,
    // or when Render instance is waking up from idle.
    throw new ApiError(
      "Cannot connect to the ApplyCanary server. If using Render free tier, the instance might be waking up from idle (~30–50s). Please wait a moment and try again.",
      0,
    );
  }

  if (!res.ok) {
    if ([502, 503, 504].includes(res.status) && retries > 0) {
      await new Promise((r) => setTimeout(r, 2000));
      return request<T>(path, init, retries - 1);
    }

    // Read the body exactly once, as text: an earlier .json() call consumes
    // the stream, so a later .text() would resolve to "" and the backend's
    // actual error message would be silently dropped.
    const text = await res.text().catch(() => "");
    let message = "";
    try {
      const body = JSON.parse(text) as { detail?: unknown; message?: unknown };
      // FastAPI puts the useful message in `detail`; gateway routers use `message`.
      if (typeof body.detail === "string" && body.detail) {
        message = body.detail;
      } else if (Array.isArray(body.detail)) {
        message = body.detail
          .map((d: any) => (typeof d === "string" ? d : d.msg || JSON.stringify(d)))
          .join(", ");
      } else if (typeof body.message === "string" && body.message) {
        message = body.message;
      }
    } catch {
      /* HTML/plain body — fall through to the status text */
    }

    // Proxy/gateway failures never carry the app's `detail`: a reverse proxy
    // returns 5xx on gateway errors, and when the backend is gone its edge
    // router answers 404 {message:"Application not found"}.
    const appGone = res.status === 404 && /application not found/i.test(text);
    if (res.status === 502 || res.status === 503 || res.status === 504 || appGone) {
      message =
        "The ApplyCanary server is starting up or temporarily idle on Render (free tier spin-up typically takes ~30–50 seconds). Please hold on a moment and retry.";
    }
    throw new ApiError(message || res.statusText || "Something went wrong. Please try again.", res.status);
  }

  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** One listing on the public events, programs or internships pages. */
export interface Opportunity {
  kind: "Event" | "Paid program" | "Internship";
  title: string;
  org: string;
  /** Human-readable timing: "15 Oct 2026 · 08:00–11:00", "Coding May–Aug". */
  when: string;
  city: string;
  tags: string[];
  source: string;
  url: string;
  desc: string;
  /** ISO event date ("" when unknown). Events count down to it. */
  date: string;
  /** Application window, paid programs only. */
  opens: string | null;
  closes: string | null;
  steps: string[] | null;
  stipend: string | null;
}

export interface Opportunities {
  items: Opportunity[];
  /** False when the live event feed could not be refreshed (items are cached). */
  events_live: boolean;
  events_updated_at: string | null;
}

const qs = (filters: object): string => {
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
  auth: {
    me: () => request<AuthUser>("/auth/me"),
    login: (email: string, password: string) =>
      request<AuthUser>("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) }),
    register: (email: string, password: string) =>
      request<AuthUser>("/auth/register", { method: "POST", body: JSON.stringify({ email, password }) }),
    logout: () => request<void>("/auth/logout", { method: "POST" }),

    // Emailed-link flows. All return 204, so there is no body to read; failures
    // surface as a thrown ApiError carrying the backend's `detail` message.
    forgotPassword: (email: string) =>
      request<void>("/auth/forgot-password", { method: "POST", body: JSON.stringify({ email }) }),
    resetPassword: (token: string, password: string) =>
      request<void>("/auth/reset-password", { method: "POST", body: JSON.stringify({ token, password }) }),
    verifyEmail: (token: string) =>
      request<void>("/auth/verify-email", { method: "POST", body: JSON.stringify({ token }) }),
    resendVerification: (email: string, password: string) =>
      request<void>("/auth/resend-verification", { method: "POST", body: JSON.stringify({ email, password }) }),
    changeEmail: (new_email: string, password: string) =>
      request<void>("/auth/change-email", { method: "POST", body: JSON.stringify({ new_email, password }) }),
    verifyEmailChange: (token: string) =>
      request<void>("/auth/verify-email-change", { method: "POST", body: JSON.stringify({ token }) }),
  },
  status: () => request<Status>("/status"),
  jobs: (filters: JobFilters = {}) => request<JobList>(`/jobs${qs(filters)}`),
  publicJobs: (filters: { q?: string; source?: string; remote_only?: boolean; sort?: "newest" | "oldest" } = {}) => request<PublicJobList>(`/public/jobs${qs(filters as JobFilters)}`),
  opportunities: () => request<Opportunities>("/public/opportunities"),
  internships: () => request<Opportunities>("/public/internships"),
  kenyaJobs: (refresh = false) =>
    request<EntryLevelFeed>(`/public/kenya-jobs${refresh ? "?refresh=true" : ""}`),
  entryLevel: (refresh = false) =>
    request<EntryLevelFeed>(`/public/entry-level${refresh ? "?refresh=true" : ""}`),
  job: (id: number) => request<JobDetail>(`/jobs/${id}`),
  review: (params: { limit?: number; offset?: number } = {}) => request<ReviewList>(`/review${qs(params)}`),
  applications: () => request<Job[]>("/applications"),
  sources: () => request<SourceHealth[]>("/sources"),
  profile: () => request<Profile>("/profile"),
  atsReport: () => request<AtsReport>("/profile/ats"),

  saveProfile: (
    body: Partial<Profile> & { github_token?: string; github_disconnect?: boolean },
  ) => request<Profile>("/profile", { method: "PUT", body: JSON.stringify(body) }),

  saveEmailSending: (payload: {
    smtp_user: string;
    smtp_app_password?: string;
    smtp_host?: string;
    smtp_port?: number;
    disconnect?: boolean;
  }) =>
    request<ActionResult>("/profile/email-sending", {
      method: "PUT",
      body: JSON.stringify(payload),
    }),
  uploadResume: (file: File) => {
    const form = new FormData();
    form.append("resume", file);
    return request<ActionResult>("/profile/resume", { method: "POST", body: form });
  },
  baseResumeDownloadUrl: () => `${API_BASE}/api/profile/resume/download`,

  tailor: (id: number) => request<ActionResult>(`/jobs/${id}/tailor`, { method: "POST" }),
  submit: (id: number, force = false) =>
    request<ActionResult>(`/jobs/${id}/submit?force=${force}`, { method: "POST" }),
  skip: (id: number) => request<ActionResult>(`/jobs/${id}/skip`, { method: "POST" }),
  save: (id: number) => request<ActionResult>(`/jobs/${id}/save`, { method: "POST" }),
  setApplyEmail: (id: number, email: string) =>
    request<ActionResult>(`/jobs/${id}/apply-email`, {
      method: "PUT",
      body: JSON.stringify({ apply_email: email }),
    }),
  prep: (id: number) => request<ActionResult>(`/jobs/${id}/prep`, { method: "POST" }),

  poll: () => request<ActionResult>("/actions/poll", { method: "POST" }),
  score: () => request<ActionResult>("/actions/score", { method: "POST" }),
  rescore: (limit = 50) =>
    request<ActionResult>(`/actions/rescore?limit=${limit}`, { method: "POST" }),
  syncGithub: () => request<ActionResult>("/actions/github", { method: "POST" }),
  discover: () => request<ActionResult>("/actions/discover", { method: "POST" }),
  embedAll: (limit = 100) =>
    request<{ ok: boolean; embedded: number }>("/actions/embed-all", {
      method: "POST", body: JSON.stringify({ limit }),
    }),

  voice: () => request<VoiceConfig>("/interview/voice"),
  tts: (text: string) =>
    request<{ audio_b64: string; content_type: string }>("/interview/tts", {
      method: "POST", body: JSON.stringify({ text }),
    }),
  startInterview: (jobId: number, mode: "speech" | "text") =>
    request<InterviewState>(`/jobs/${jobId}/interview/start`, {
      method: "POST", body: JSON.stringify({ mode }),
    }),
  answerInterview: (
    jobId: number, sessionId: number,
    payload: { text?: string; audio_b64?: string; duration_seconds?: number },
  ) =>
    request<InterviewState>(`/jobs/${jobId}/interview/sessions/${sessionId}/answer`, {
      method: "POST", body: JSON.stringify(payload),
    }),
  interviewState: (sessionId: number) =>
    request<InterviewState>(`/interview/sessions/${sessionId}`),

  similarJobs: (jobId: number, limit = 6) =>
    request<{ jobs: SimilarJob[]; embedded: boolean }>(`/jobs/${jobId}/similar?limit=${limit}`),
  semanticSearch: (q: string, limit = 12) =>
    request<{ query: string; jobs: SimilarJob[] }>(`/jobs/search/semantic?q=${encodeURIComponent(q)}&limit=${limit}`),
  memory: () => request<MemoryIndex>("/memory"),

  // Feedback & contact (works for logged-in users & guest visitors)
  submitFeedback: (payload: { name?: string; email?: string; category?: string; message: string }) =>
    request<{ ok: boolean; message: string }>("/public/feedback", {
      method: "POST",
      body: JSON.stringify(payload),
    }),

  // Admin telemetry & service control (admin only)
  adminFeedback: (status = "all", limit = 50, offset = 0) =>
    request<FeedbackList>(`/admin/feedback?status=${status}&limit=${limit}&offset=${offset}`),
  updateFeedback: (id: number, payload: { status?: string; admin_notes?: string }) =>
    request<{ ok: boolean; message: string }>(`/admin/feedback/${id}`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteFeedback: (id: number) =>
    request<{ ok: boolean; message: string }>(`/admin/feedback/${id}`, {
      method: "DELETE",
    }),
  systemStatus: () => request<SystemStatus>("/admin/system-status"),
  emailEvents: (email: string) =>
    request<EmailDeliveryReport>(`/admin/email-events?email=${encodeURIComponent(email)}`),
  setUserRole: (userId: number, payload: { is_admin?: boolean; is_active?: boolean }) =>
    request<{ ok: boolean }>(`/admin/users/${userId}/role`, {
      method: "PATCH",
      body: JSON.stringify(payload),
    }),
  deleteUser: (userId: number) =>
    request<{ ok: boolean }>(`/admin/users/${userId}`, {
      method: "DELETE",
    }),
  updateLlmKeys: (keys: Record<string, string>) =>
    request<{ ok: boolean; updated: string[]; note: string }>("/admin/settings/llm-keys", {
      method: "PATCH",
      body: JSON.stringify(keys),
    }),
  getSourceRuns: (limit = 50) =>
    request<Array<{
      id: number;
      source: string;
      started_at: string;
      duration_ms: number;
      found: number;
      new_jobs: number;
      duplicates: number;
      ok: boolean;
      error: string;
    }>>(`/admin/source-runs?limit=${limit}`),
  getInvites: () =>
    request<Array<{
      id: number;
      code: string;
      used: boolean;
      used_at: string | null;
      expires_at: string | null;
      created_at: string;
    }>>("/admin/invites"),
  createInvite: () =>
    request<{ ok: boolean; code: string; link: string }>("/admin/invites", {
      method: "POST",
    }),
  revokeInvite: (code: string) =>
    request<{ ok: boolean }>(`/admin/invites/${code}`, {
      method: "DELETE",
    }),
  testLlm: (provider?: string) =>
    request<TestLlmResult>("/admin/test-llm", {
      method: "POST",
      body: JSON.stringify({ provider }),
    }),
  triggerJob: (jobName: string) =>
    request<{ ok: boolean; message: string }>(`/admin/trigger-job/${jobName}`, {
      method: "POST",
    }),
  watchOpp: (data: {
    url: string;
    title: string;
    org: string;
    kind: string;
    deadline: string | null;
  }) => watchOpp(data),
  unwatchOpp: (url: string) => unwatchOpp(url),
  getWatched: () => getWatched(),
  vapidKey: () => vapidKey(),
};

export interface FeedbackItem {
  id: number;
  user_id: number | null;
  name: string;
  email: string;
  category: string;
  message: string;
  status: "new" | "in_progress" | "resolved";
  admin_notes: string;
  created_at: string;
}

export interface FeedbackList {
  items: FeedbackItem[];
  total: number;
  new_count: number;
}

export interface LlmProviderStatus {
  name: string;
  key: string;
  configured: boolean;
  model: string;
  is_active: boolean;
  on_cooldown: boolean;
}

export interface ConnectorHealth {
  source: string;
  last_run: string | null;
  duration_ms: number;
  found: number;
  new_jobs: number;
  ok: boolean;
  error: string;
}

export interface AdminUserItem {
  id: number;
  email: string;
  is_admin: boolean;
  is_superuser: boolean;
  is_active: boolean;
  email_verified: boolean;
  plan: "paid" | "free";
  last_login_at: string | null;
  created_at: string;
}

export interface SystemStatus {
  database: {
    ok: boolean;
    dialect: string;
    vector_backend: string;
    jobs_count: number;
    applications_count: number;
    sessions_count: number;
    feedback_new: number;
  };
  scheduler: {
    running: boolean;
    jobs: Array<{ id: string; name: string; next_run: string | null }>;
  };
  users: {
    total: number;
    active: number;
    verified: number;
    admin: number;
    user_list?: AdminUserItem[];
  };
  connectors: ConnectorHealth[];
  llm: {
    available: boolean;
    active_provider: string;
    providers: LlmProviderStatus[];
  };
  email: {
    configured: boolean;
    backend: string;
    /** Sender address from EMAIL_FROM; a free mailbox (gmail.com, …) is a
     *  common reason receivers spam-folder verification mail. */
    from: string;
    /** Absolute origin for verify/reset links; empty means sends are skipped. */
    link_base: string;
    verification_required: boolean;
  };
}

export interface EmailDeliveryEvent {
  date: string;
  /** Brevo event: requests, delivered, deferred, blocked, hardBounces, spam… */
  event: string;
  /** Bounce reason, when Brevo reports one. */
  reason: string;
  subject: string;
  message_id: string;
}

export interface EmailDeliveryReport {
  ok: boolean;
  email: string;
  events: EmailDeliveryEvent[];
  config: {
    backend: string;
    from: string;
    link_base: string;
    verification_required: boolean;
  };
  error: string;
}

export interface TestLlmResult {
  ok: boolean;
  provider: string;
  model?: string;
  latency_ms: number;
  reply?: string;
  error?: string;
}

export interface WatchedOpp {
  id: number;
  opp_url: string;
  opp_title: string;
  opp_org: string;
  opp_kind: string;
  deadline: string | null;
  created_at: string;
}

export const watchOpp = (data: {
  url: string;
  title: string;
  org: string;
  kind: string;
  deadline: string | null;
}) => request<{ ok: boolean }>("POST", "/api/opportunities/watch", data);

export const unwatchOpp = (url: string) =>
  request<{ ok: boolean }>("DELETE", "/api/opportunities/watch", { url });

export const getWatched = () =>
  request<WatchedOpp[]>("GET", "/api/opportunities/watched");

export const vapidKey = () =>
  request<{ publicKey: string }>("GET", "/api/push/vapid-public-key");


