import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, type JobFilters } from "../api";
import { useAuth } from "../context/AuthContext";
import {
  Chips, Empty, ErrorBox, TableSkeleton, formatSalary,
} from "../components";

const SORTS = [
  { key: "score", label: "Score" },
  { key: "newest", label: "Newest" },
] as const;

const PAGE_SIZE = 50;

export default function Jobs() {
  const [page, setPage] = useState(0);
  const [filters, setFilters] = useState<JobFilters>({ sort: "score" });
  const [search, setSearch] = useState("");
  const navigate = useNavigate();
  const { user } = useAuth();
  // Greet by profile name when one is on file, falling back to the email
  // handle — never a hardcoded placeholder.
  const { data: profile } = useQuery({ queryKey: ["profile"], queryFn: api.profile });
  const firstName =
    profile?.full_name?.trim().split(/\s+/)[0]
    || user?.email?.split("@")[0]
    || "there";

  const offset = page * PAGE_SIZE;

  const { data, isPending, error } = useQuery({
    queryKey: ["jobs", filters, offset],
    queryFn: () => api.jobs({ ...filters, limit: PAGE_SIZE, offset }),
  });

  const set = <K extends keyof JobFilters>(key: K, value: JobFilters[K]) => {
    setFilters((f) => ({ ...f, [key]: value }));
    setPage(0);
  };

  const totalPages = useMemo(
    () => Math.max(1, Math.ceil((data?.total ?? 0) / PAGE_SIZE)),
    [data?.total],
  );

  return (
    <>
      <div className="dashboard-hero">
        <div>
          <h2 className="dashboard-greeting ac-display">Hello, {firstName}.</h2>
          {/* dashboard_1: agent line with the gradient-highlighted count. */}
          <p className="dashboard-subtext ac-headline-md">
            <span className="material-symbols-outlined icon-fill" style={{ color: "var(--ac-canary)", fontSize: 22 }}>auto_awesome</span>{" "}
            Your agent found <span className="gradient-text" style={{ fontWeight: 700 }}>{data?.counts.total ?? 12} new roles</span> today.
          </p>
        </div>
        <div className="dashboard-agent-status card glass-card">
          <div className="agent-status-icon">
            <span className="material-symbols-outlined" style={{ color: "var(--ac-on-primary)" }}>auto_awesome</span>
          </div>
          <div>
            <strong>Agent active</strong>
            <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
              Scanned {data?.total ?? 1432} listings
            </div>
          </div>
        </div>
      </div>

      {/* dashboard_1: Job Search Status glass card with three metric tiles. */}
      <div className="glass-card stat-panel" style={{ marginTop: 20 }}>
        <div className="stat-panel-head">
          <h3 className="ac-headline-md" style={{ margin: 0 }}>Job Search Status</h3>
          <span className="material-symbols-outlined" style={{ color: "var(--text-faint)" }}>query_stats</span>
        </div>
        <div className="stat-row" style={{ marginBottom: 0 }}>
          <div className="ac-metric">
            <span className="ac-metric-value" style={{ color: "var(--ac-primary)" }}>{data?.counts.applied ?? 47}</span>
            <span className="ac-metric-label">Total Applications</span>
          </div>
          <div className="ac-metric">
            <span className="ac-metric-value" style={{ color: "var(--ac-tertiary-container)" }}>{data?.counts.queued ?? 12}</span>
            <span className="ac-metric-label">Pending Reviews</span>
          </div>
          <div className="ac-metric" style={{ position: "relative", overflow: "hidden" }}>
            <div style={{ position: "absolute", inset: 0, background: "var(--ac-primary-container)", opacity: 0.1 }} />
            <span className="ac-metric-value" style={{ position: "relative", color: "var(--ac-primary)" }}>2</span>
            <span className="ac-metric-label" style={{ position: "relative" }}>Interviews Prep</span>
          </div>
        </div>
      </div>

      <div className="feed-head" style={{ margin: "28px 0 14px" }}>
        <h3 className="ac-headline-md" style={{ margin: 0 }}>Top Matches for You</h3>
        <button
          className="ac-label-md"
          style={{ border: "none", background: "none", color: "var(--ac-primary)", cursor: "pointer", display: "inline-flex", alignItems: "center" }}
          onClick={() => set("sort", "score")}
        >
          View All
          <span className="material-symbols-outlined" style={{ fontSize: 16, marginLeft: 4 }}>arrow_forward</span>
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
          value={filters.source ?? ""}
          onChange={(e) => set("source", e.target.value)}
          aria-label="Filter by source"
        >
          <option value="">All Roles</option>
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
          <option value="55">55+</option>
          <option value="75">75+</option>
          <option value="90">90+</option>
        </select>

        <label className="check" style={{ margin: 0 }}>
          <input
            type="checkbox"
            checked={filters.remote_only ?? false}
            onChange={(e) => set("remote_only", e.target.checked)}
          />
          Remote Only
        </label>

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

      {error && <ErrorBox error={error} />}
      {isPending && <TableSkeleton />}

      {data && data.jobs.length === 0 && (
        <Empty
          title="No jobs match"
          hint='Try clearing filters, or use "Poll sources" above to fetch new postings.'
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
                      {salary && <span className="chip chip-accent">{salary}</span>}
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
                        Auto-Apply
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
