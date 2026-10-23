import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api, type JobFilters } from "../api";
import { useAuth } from "../context/AuthContext";
import {
  Chips, Empty, ErrorBox, TableSkeleton, formatSalary, relTime,
} from "../components";

const SORTS = [
  { key: "score", label: "Score" },
  { key: "newest", label: "Newest" },
] as const;

const PAGE_SIZE = 50;

export default function Jobs() {
  const [params] = useSearchParams();
  const [page, setPage] = useState(0);
  const [filters, setFilters] = useState<JobFilters>({
    sort: "score",
    prep_only: params.get("prep") === "1",
    posted_within: 30,
  });
  const [search, setSearch] = useState("");
  const navigate = useNavigate();
const { user } = useAuth();
// Greet by profile name when one is on file, falling back to the email
// handle — never a hardcoded placeholder.
const { data: profile } = useQuery({ queryKey: ["profile"], queryFn: api.profile });
const qc = useQueryClient();
const firstName =
    profile?.full_name?.trim().split(/\s+/)[0]
    || user?.email?.split("@")[0]
    || "there";

  const offset = page * PAGE_SIZE;

  const { data, isPending, error } = useQuery({
    queryKey: ["jobs", filters, offset],
    queryFn: () => api.jobs({ ...filters, limit: PAGE_SIZE, offset }),
  });

  const saveJob = useMutation({
    mutationFn: (id: number) => api.save(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["jobs"] });
      void qc.invalidateQueries({ queryKey: ["job"] });
    },
  });

  const set = <K extends keyof JobFilters>(key: K, value: JobFilters[K]) => {
    setFilters((f) => ({ ...f, [key]: value }));
    setPage(0);
  };

  const hasActiveFilters = Boolean(
    search ||
    filters.q ||
    filters.experience_level ||
    filters.work_arrangement ||
    filters.location ||
    filters.employment_type ||
    filters.visa_sponsorship ||
    filters.min_score ||
    filters.source ||
    filters.status ||
    filters.prep_only ||
    filters.remote_only ||
    (filters.posted_within && filters.posted_within !== 30)
  );

  const resetFilters = () => {
    setSearch("");
    setFilters({
      sort: "score",
      posted_within: 30,
    });
    setPage(0);
  };

  const totalPages = useMemo(
    () => Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE)),
    [data?.total],
  );
  const scored = (data?.counts.scored ?? 0) + (data?.counts.rejected ?? 0);
  const pending = data?.counts.new ?? 0;
  const bestScore = useMemo(
    () => Math.max(0, ...(data?.jobs ?? []).map((j) => j.score?.total ?? 0)),
    [data?.jobs],
  );

  return (
    <>
      <div className="dashboard-hero">
        <div>
          <h2 className="dashboard-greeting ac-display">Hello, {firstName}.</h2>
          {/* dashboard_1: agent line with the gradient-highlighted count. */}
          <p className="dashboard-subtext ac-headline-md">
            <span className="material-symbols-outlined icon-fill" style={{ color: "var(--ac-canary)", fontSize: 22 }}>auto_awesome</span>{" "}
            Your agent found <span className="gradient-text" style={{ fontWeight: 700 }}>{data?.counts.total ?? 0} live roles</span> today
            {data ? ` · ${scored} scored${pending ? ` · ${pending} awaiting scoring` : ""}` : ""}.
          </p>
        </div>
        <div className="dashboard-agent-status card glass-card">
          <div className="agent-status-icon">
            <span className="material-symbols-outlined" style={{ color: "var(--ac-on-primary)" }}>auto_awesome</span>
          </div>
          <div>
            <strong>Agent active</strong>
            <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
              {data?.jobs.length ?? 0} matches shown{bestScore ? ` · best ${Math.round(bestScore)}%` : ""}
            </div>
          </div>
        </div>
      </div>

      {/* Relevance filters only exist once the profile carries the facts they
          compare against. Say which ones are off instead of silently serving a
          worldwide, any-seniority feed. */}
      {profile && (() => {
        const missing = [
          !profile.location && "your location",
          profile.target_locations.length === 0 && "target locations",
          profile.years_experience === null && "years of experience",
        ].filter(Boolean) as string[];
        if (missing.length === 0) return null;
        return (
          <div className="card" style={{ marginTop: 20, padding: 14, display: "flex", gap: 12, alignItems: "baseline", flexWrap: "wrap" }}>
            <strong style={{ fontSize: 14 }}>Sharpen this feed</strong>
            <span className="cell-dim" style={{ fontSize: 13, flex: "1 1 320px" }}>
              Missing {missing.join(", ")}. Until they are set, remote roles restricted to another
              country and roles above your experience level cannot be filtered out.
            </span>
            <Link to="/profile" className="btn-ghost" style={{ fontSize: 12, padding: "3px 10px" }}>
              Update profile
            </Link>
          </div>
        );
      })()}

      {/* dashboard_1: Job Search Status glass card with three metric tiles. */}
      <div className="glass-card stat-panel" style={{ marginTop: 20 }}>
        <div className="stat-panel-head">
          <h3 className="ac-headline-md" style={{ margin: 0 }}>Job Search Status</h3>
          <span className="material-symbols-outlined" style={{ color: "var(--text-faint)" }}>query_stats</span>
        </div>
        <div className="stat-row" style={{ marginBottom: 0 }}>
          <Link to="/applications" className="ac-metric ac-metric-link">
            <span className="ac-metric-value" style={{ color: "var(--ac-primary)" }}>{data?.counts.applied ?? 0}</span>
            <span className="ac-metric-label">Total Applications</span>
          </Link>
          <Link to="/review" className="ac-metric ac-metric-link">
            <span className="ac-metric-value" style={{ color: "var(--ac-tertiary-container)" }}>{data?.counts.queued ?? 0}</span>
            <span className="ac-metric-label">Pending Reviews</span>
          </Link>
          <Link
            to="/jobs?prep=1"
            onClick={() => set("prep_only", true)}
            className="ac-metric ac-metric-link"
            style={{ position: "relative", overflow: "hidden" }}
          >
            <div style={{ position: "absolute", inset: 0, background: "var(--ac-primary-container)", opacity: 0.1 }} />
            <span className="ac-metric-value" style={{ position: "relative", color: "var(--ac-primary)" }}>{data?.counts.preps ?? 0}</span>
            <span className="ac-metric-label" style={{ position: "relative" }}>Interview Preps</span>
          </Link>
        </div>
      </div>

      <div className="feed-head" style={{ margin: "28px 0 14px" }}>
        <h3 className="ac-headline-md" style={{ margin: 0 }}>
          {filters.include_all ? "All Live Roles" : "Top Matches for You"}
        </h3>
        <button
          className="ac-label-md"
          style={{ border: "none", background: "none", color: "var(--ac-primary)", cursor: "pointer", display: "inline-flex", alignItems: "center" }}
          onClick={() => set("include_all", !filters.include_all)}
        >
          {filters.include_all ? "Top Matches" : "View All"}
          <span className="material-symbols-outlined" style={{ fontSize: 16, marginLeft: 4 }}>
            {filters.include_all ? "arrow_back" : "arrow_forward"}
          </span>
        </button>
      </div>

      <form
        className="toolbar"
        onSubmit={(e) => {
          e.preventDefault();
          set("q", search);
        }}
        role="search"
      >
        <input
          type="search"
          placeholder="Search roles, skills, or companies…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Search jobs"
        />

        <select
          value={filters.experience_level ?? ""}
          onChange={(e) => set("experience_level", e.target.value || undefined)}
          aria-label="Filter by experience level"
        >
          <option value="">All Experience Levels</option>
          <option value="entry">Entry / Junior (0-2 yrs)</option>
          <option value="mid">Mid Level (2-5 yrs)</option>
          <option value="senior">Senior (5+ yrs)</option>
          <option value="lead">Staff / Principal / Lead</option>
          <option value="manager">Manager / Director</option>
        </select>

        <select
          value={filters.work_arrangement ?? (filters.remote_only ? "remote" : "")}
          onChange={(e) => {
            const val = e.target.value;
            set("work_arrangement", val || undefined);
            if (val === "remote") {
              set("remote_only", true);
            } else {
              set("remote_only", false);
            }
          }}
          aria-label="Filter by work arrangement"
        >
          <option value="">All Work Arrangements</option>
          <option value="remote">Remote Only</option>
          <option value="hybrid">Hybrid</option>
          <option value="onsite">Onsite</option>
        </select>

        <select
          value={filters.employment_type ?? ""}
          onChange={(e) => set("employment_type", e.target.value || undefined)}
          aria-label="Filter by job type"
        >
          <option value="">All Job Types</option>
          <option value="full-time">Full-time</option>
          <option value="contract">Contract</option>
          <option value="internship">Internship</option>
          <option value="part-time">Part-time</option>
        </select>

        <select
          value={String(filters.posted_within ?? 0)}
          onChange={(e) => set("posted_within", Number(e.target.value) || undefined)}
          aria-label="Filter by posting date"
        >
          <option value="0">Any time</option>
          <option value="1">Past 24 hours</option>
          <option value="3">Past 3 days</option>
          <option value="7">Past week</option>
          <option value="30">Past month</option>
          <option value="90">Past 3 months</option>
        </select>

        <select
          value={filters.source ?? ""}
          onChange={(e) => set("source", e.target.value)}
          aria-label="Filter by source"
        >
          <option value="">All Sources</option>
          {data?.sources.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>

        <select
          value={filters.status ?? ""}
          onChange={(e) => set("status", e.target.value)}
          aria-label="Filter by status"
        >
          <option value="">Any Status</option>
          {["new", "scored", "queued", "applied", "skipped", "rejected"].map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>

        <select
          value={String(filters.min_score ?? 0)}
          onChange={(e) => set("min_score", Number(e.target.value))}
          aria-label="Minimum score"
        >
          <option value="0">Any Score</option>
          <option value="55">55+ (Good match)</option>
          <option value="75">75+ (Strong match)</option>
          <option value="90">90+ (Top match)</option>
        </select>

        <div className="seg" role="group" aria-label="Sort order">
          {SORTS.map((s) => (
            <button
              key={s.key}
              type="button"
              aria-pressed={filters.sort === s.key}
              onClick={() => set("sort", s.key)}
            >
              {s.label}
            </button>
          ))}
        </div>
      </form>

      {/* Quick filter pills */}
      <div className="ac-chip-row" style={{ marginTop: -4, marginBottom: 14 }}>
        <button
          type="button"
          className={`ac-chip ${filters.visa_sponsorship ? "is-active" : ""}`}
          onClick={() => set("visa_sponsorship", !filters.visa_sponsorship)}
          style={{ cursor: "pointer", border: "1px solid var(--ac-outline-variant)" }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 15 }}>flight</span>
          Visa &amp; Relocation
        </button>

        <button
          type="button"
          className={`ac-chip ${filters.work_arrangement === "remote" || filters.remote_only ? "is-active" : ""}`}
          onClick={() => {
            const next = filters.work_arrangement !== "remote";
            set("work_arrangement", next ? "remote" : undefined);
            set("remote_only", next);
          }}
          style={{ cursor: "pointer", border: "1px solid var(--ac-outline-variant)" }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 15 }}>public</span>
          Remote Only
        </button>

        <button
          type="button"
          className={`ac-chip ${filters.location === "Kenya" ? "is-active" : ""}`}
          onClick={() => set("location", filters.location === "Kenya" ? undefined : "Kenya")}
          style={{ cursor: "pointer", border: "1px solid var(--ac-outline-variant)" }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 15 }}>location_on</span>
          Kenya
        </button>

        <button
          type="button"
          className={`ac-chip ${filters.experience_level === "entry" ? "is-active" : ""}`}
          onClick={() => set("experience_level", filters.experience_level === "entry" ? undefined : "entry")}
          style={{ cursor: "pointer", border: "1px solid var(--ac-outline-variant)" }}
        >
          🌱 Entry / Junior
        </button>

        <button
          type="button"
          className={`ac-chip ${filters.experience_level === "mid" ? "is-active" : ""}`}
          onClick={() => set("experience_level", filters.experience_level === "mid" ? undefined : "mid")}
          style={{ cursor: "pointer", border: "1px solid var(--ac-outline-variant)" }}
        >
          ⚡ Mid-Level
        </button>

        <button
          type="button"
          className={`ac-chip ${filters.experience_level === "senior" ? "is-active" : ""}`}
          onClick={() => set("experience_level", filters.experience_level === "senior" ? undefined : "senior")}
          style={{ cursor: "pointer", border: "1px solid var(--ac-outline-variant)" }}
        >
          🚀 Senior (5+ yrs)
        </button>

        <button
          type="button"
          className={`ac-chip ${filters.min_score === 75 ? "is-active" : ""}`}
          onClick={() => set("min_score", filters.min_score === 75 ? 0 : 75)}
          style={{ cursor: "pointer", border: "1px solid var(--ac-outline-variant)" }}
        >
          🎯 75%+ Matches
        </button>

        <button
          type="button"
          className={`ac-chip ${filters.prep_only ? "is-active" : ""}`}
          onClick={() => set("prep_only", !filters.prep_only)}
          style={{ cursor: "pointer", border: "1px solid var(--ac-outline-variant)" }}
        >
          <span className="material-symbols-outlined" style={{ fontSize: 15 }}>mic</span>
          Prep Ready
        </button>

        {hasActiveFilters && (
          <button
            type="button"
            className="ac-chip"
            onClick={resetFilters}
            style={{ cursor: "pointer", background: "var(--surface-raised)", color: "var(--ac-primary)", borderColor: "var(--ac-primary)" }}
          >
            <span className="material-symbols-outlined" style={{ fontSize: 15 }}>close</span>
            Reset filters
          </button>
        )}
      </div>

      {error && <ErrorBox error={error} />}
      {isPending && <TableSkeleton />}

      {data && data.jobs.length === 0 && (
        <Empty
          title={filters.prep_only
            ? "No interview preps yet"
            : pending > 0 && !filters.q
              ? "No scored matches yet"
              : filters.posted_within && !filters.q
                ? "No roles in this date range"
                : "No jobs match"}
          hint={filters.prep_only
            ? "Open a job and generate its interview prep to see it here."
            : pending > 0 && !filters.q
              ? `${pending} of ${data.counts.total} live roles are still being scored. The agent scores in batches every couple of minutes — no top match is shown until a role has been judged against your resume.`
              : filters.posted_within && !filters.q
                ? "Older posts are hidden by default. Choose a wider range, or \"Any time\" to include them."
                : 'Try clearing filters, or use "Poll sources" above to fetch new postings.'}
        />
      )}

      {data && data.jobs.length > 0 && (
        <div className="discovery-layout">
          <div className="job-feed">
            {data.jobs.map((job) => {
              const salary = formatSalary(
                job.salary_min, job.salary_max, job.salary_currency, job.salary_is_estimate,
              );
              const total = job.score ? Math.round(job.score.total) : null;
              const band =
                total === null ? "none" : total >= 75 ? "strong" : total >= 55 ? "mid" : "weak";
              return (
                <article
                  key={job.id}
                  className="job-card discovery-card"
                  onClick={() => navigate(`/job/${job.id}`)}
                  tabIndex={0}
                  role="link"
                  onKeyDown={(e) => {
                    if (e.key === "Enter") navigate(`/job/${job.id}`);
                  }}
                >
                  <div className="discovery-card-top">
                    <div className="job-card-company">
                      <div className="company-logo-placeholder">
                        <span className="material-symbols-outlined">terminal</span>
                      </div>
                      <div>
                        <h3 className="job-card-title">{job.title}</h3>
                        <div style={{ fontSize: 13, color: "var(--text-dim)", marginTop: 2 }}>
                          <strong>{job.company}</strong> • {job.is_remote ? "Remote" : job.location || "Location not stated"}
                        </div>
                      </div>
                    </div>
                    {/* dashboard_1 match badge: canary pill, fire icon. */}
                    <div className={`match-badge match-badge-${band}`}>
                      <span className="material-symbols-outlined" style={{ fontSize: 14 }}>
                        {band === "strong" ? "local_fire_department" : band === "mid" ? "check_circle" : "star"}
                      </span>
                      {total !== null ? `${total}% Match` : "Scoring"}
                    </div>
                  </div>

                  {job.score?.reasoning && (
                    <div className="ai-reasoning" style={{ margin: "12px 0 8px" }}>
                      <span className="material-symbols-outlined" aria-hidden="true">auto_awesome</span>
                      <p>
                        <strong>AI Analysis:</strong> {job.score.reasoning}
                      </p>
                    </div>
                  )}

                  <div className="discovery-card-bottom">
                    <div className="job-card-meta">
                      {(() => {
                        const age = relTime(job.posted_at ?? job.first_seen_at);
                        return (
                          <span className="job-card-age">
                            <span className="material-symbols-outlined" aria-hidden="true">schedule</span>
                            {age === "now" ? "Posted just now" : `Posted ${age} ago`}
                          </span>
                        );
                      })()}
                      {salary && <span className="chip chip-accent">{salary}</span>}
                      {job.applicants !== null && (
                        <span className="chip" title="Applicants reported by the source">
                          {job.applicants} applicant{job.applicants === 1 ? "" : "s"}
                        </span>
                      )}
                      {job.employment_type && (
                        <span className="chip">{job.employment_type}</span>
                      )}
                      {job.benefits.length > 0 && (
                        <Chips items={job.benefits} variant="accent" max={3} />
                      )}
                      {job.score?.matched_keywords.length ? (
                        <Chips items={job.score.matched_keywords} variant="hit" max={3} />
                      ) : null}
                      {job.score?.missing_keywords.length ? (
                        <Chips items={job.score.missing_keywords} variant="miss" max={2} />
                      ) : null}
                    </div>

                    <div className="discovery-card-actions">
                      <button
                        className="btn-primary btn-sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          if (job.apply_url) window.open(job.apply_url, "_blank");
                          else navigate(`/job/${job.id}`);
                        }}
                      >
                        Apply
                      </button>
                      <button
                        className="btn-ai btn-sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate(`/job/${job.id}`);
                        }}
                      >
                        Review
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation();
                          saveJob.mutate(job.id);
                        }}
                        className={`btn-sm ${job.status === "saved" ? "btn-primary" : "btn-ghost"}`}
                        title={job.status === "saved" ? "Remove from saved" : "Save for later"}
                        disabled={saveJob.isPending}
                      >
                        {job.status === "saved" ? "★ Saved" : "☆ Save"}
                      </button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>

          <aside className="market-insights">
            <div className="card daily-digest-card" style={{ padding: 16, borderRadius: 24 }}>
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 14, paddingBottom: 12, borderBottom: "1px solid var(--ac-surface-variant)" }}>
                <div style={{ width: 32, height: 32, borderRadius: "50%", background: "var(--ac-canary)", display: "grid", placeItems: "center" }}>
                  <span className="material-symbols-outlined" style={{ color: "var(--ac-secondary)", fontSize: 18 }}>smart_toy</span>
                </div>
                <h3 className="card-title" style={{ margin: 0 }}>Daily Digest</h3>
              </div>

              <div className="timeline">
                <div className="timeline-item">
                  <div className="timeline-time">10:45 AM</div>
                  <div className="timeline-badge badge-purple">
                    <span className="timeline-dot" style={{ background: "var(--ac-primary)" }} />
                  </div>
                  <div className="timeline-content">
                    <strong>Scored 42 newly posted roles</strong>
                    <p>Matching roles ranked against your resume and preferences.</p>
                  </div>
                </div>

                <div className="timeline-item">
                  <div className="timeline-time">09:15 AM</div>
                  <div className="timeline-badge badge-purple">
                    <span className="timeline-dot" style={{ background: "var(--ac-tertiary-container)" }} />
                  </div>
                  <div className="timeline-content">
                    <strong>CV Tailored</strong>
                    <p>Tailored resume generated for your next application.</p>
                  </div>
                </div>

                <div className="timeline-item">
                  <div className="timeline-time">08:00 AM</div>
                  <div className="timeline-badge badge-purple">
                    <span className="timeline-dot" style={{ background: "var(--ac-secondary)" }} />
                  </div>
                  <div className="timeline-content">
                    <strong>Polled 5 target companies</strong>
                    <p>ATS systems checked for application status updates.</p>
                  </div>
                </div>
              </div>

              {/* dashboard_1: gradient agent-activity meter at the card foot. */}
              <div style={{ marginTop: 18, paddingTop: 14, borderTop: "1px solid var(--ac-surface-variant)" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
                  <span className="ac-label-md" style={{ color: "var(--text-dim)" }}>Agent Activity</span>
                  <span className="material-symbols-outlined icon-fill" style={{ color: "var(--ac-primary)", fontSize: 16, animation: "spin 2s linear infinite" }}>autorenew</span>
                </div>
                <div style={{ height: 6, background: "var(--ac-surface-variant)", borderRadius: 999, overflow: "hidden" }}>
                  <div style={{ height: "100%", width: "75%", background: "linear-gradient(90deg, var(--ac-primary), var(--ac-primary-container))", borderRadius: 999 }} />
                </div>
              </div>
            </div>
          </aside>
        </div>
      )}

      {data && data.total > PAGE_SIZE && (
        <Pagination
          page={page}
          totalPages={totalPages}
          total={data.total}
          showing={data.jobs.length}
          onPageChange={setPage}
        />
      )}
    </>
  );
}


function Pagination({
  page,
  totalPages,
  total,
  showing,
  onPageChange,
}: {
  page: number;
  totalPages: number;
  total: number;
  showing: number;
  onPageChange: (p: number) => void;
}) {
  return (
    <div className="pagination">
      <span className="pagination-info">
        Showing {showing} of {total}
      </span>
      <div className="pagination-controls">
        <button
          className="btn-sm"
          disabled={page === 0}
          onClick={() => onPageChange(0)}
        >
          « First
        </button>
        <button
          className="btn-sm"
          disabled={page === 0}
          onClick={() => onPageChange(page - 1)}
        >
          ‹ Prev
        </button>
        <span className="pagination-page">
          Page {page + 1} of {totalPages}
        </span>
        <button
          className="btn-sm"
          disabled={page >= totalPages - 1}
          onClick={() => onPageChange(page + 1)}
        >
          Next ›
        </button>
        <button
          className="btn-sm"
          disabled={page >= totalPages - 1}
          onClick={() => onPageChange(totalPages - 1)}
        >
          Last »
        </button>
      </div>
    </div>
  );
}
