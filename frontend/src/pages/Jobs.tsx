import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, type JobFilters } from "../api";
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
          <h2 className="dashboard-greeting">Hello, Alex.</h2>
          <p className="dashboard-subtext">
            Your AI agent is actively searching and preparing your career moves.
          </p>
        </div>
        <div className="dashboard-agent-status card">
          <div className="agent-status-icon">
            <span className="material-symbols-outlined" style={{ color: "var(--ac-on-primary)" }}>auto_awesome</span>
          </div>
          <div>
            <strong>Found {data?.counts.total ?? 12} new roles today</strong>
            <div style={{ fontSize: 12, color: "var(--text-dim)" }}>
              Agent scanned {data?.total ?? 1432} listings
            </div>
          </div>
        </div>
      </div>

      <div className="stat-row" style={{ marginTop: 20 }}>
        <div className="stat">
          <div className="stat-icon-row">
            <span className="material-symbols-outlined">send</span>
          </div>
          <div className="stat-value">{data?.counts.applied ?? 47}</div>
          <div className="stat-label">Total Applications</div>
          <span className="stat-badge">+3 this week</span>
        </div>
        <div className="stat">
          <div className="stat-icon-row">
            <span className="material-symbols-outlined">pending_actions</span>
          </div>
          <div className="stat-value">{data?.counts.queued ?? 12}</div>
          <div className="stat-label">Pending Reviews</div>
          <span className="stat-badge muted">Awaiting response</span>
        </div>
        <div className="stat">
          <div className="stat-icon-row">
            <span className="material-symbols-outlined">record_voice_over</span>
          </div>
          <div className="stat-value">2</div>
          <div className="stat-label">Interviews Prep</div>
          <span className="stat-link" onClick={() => navigate("/memory")}>Start Studio &rarr;</span>
        </div>
      </div>

      <div style={{ margin: "24px 0 14px", display: "flex", alignItems: "center", gap: 8 }}>
        <span className="material-symbols-outlined" style={{ color: "var(--ac-canary)", fontSize: 22 }}>star</span>
        <h3 style={{ margin: 0, fontSize: 20, fontWeight: 700 }}>Top Matches for You</h3>
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
                        {job.company?.[0]?.toUpperCase() ?? "C"}
                      </div>
                      <div>
                        <h3 className="job-card-title">{job.title}</h3>
                        <div style={{ fontSize: 13, color: "var(--text-dim)", marginTop: 2 }}>
                          <strong>{job.company}</strong> • {job.is_remote ? "Remote" : job.location || "Location not stated"}
                        </div>
                      </div>
                    </div>
                    <div className={`match-badge match-badge-${band}`}>
                      <span className="material-symbols-outlined" style={{ fontSize: 16 }}>
                        {band === "strong" ? "check_circle" : "auto_awesome"}
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
                        Auto-Apply ⚡
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
            <div className="card daily-digest-card">
              <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 16 }}>
                <span className="material-symbols-outlined" style={{ color: "var(--ac-primary)" }}>history</span>
                <h3 className="card-title" style={{ margin: 0 }}>Daily Digest</h3>
              </div>

              <div className="timeline">
                <div className="timeline-item">
                  <div className="timeline-time">10:42 AM</div>
                  <div className="timeline-badge badge-purple">
                    <span className="material-symbols-outlined" style={{ fontSize: 14 }}>bolt</span>
                  </div>
                  <div className="timeline-content">
                    <strong>Auto-Applied to Acme Corp</strong>
                    <p>Tailored resume sent for Senior UX Designer role based on 98% match.</p>
                  </div>
                </div>

                <div className="timeline-item">
                  <div className="timeline-time">09:15 AM</div>
                  <div className="timeline-badge badge-yellow">
                    <span className="material-symbols-outlined" style={{ fontSize: 14 }}>edit</span>
                  </div>
                  <div className="timeline-content">
                    <strong>CV Tailored</strong>
                    <p>Agent highlighted 'Design Systems' experience for upcoming tech roles.</p>
                  </div>
                </div>

                <div className="timeline-item">
                  <div className="timeline-time">Yesterday</div>
                  <div className="timeline-badge badge-gray">
                    <span className="material-symbols-outlined" style={{ fontSize: 14 }}>psychology</span>
                  </div>
                  <div className="timeline-content">
                    <strong>Feedback Remembered</strong>
                    <p>Noted your preference to avoid roles requiring 100% travel.</p>
                  </div>
                </div>

                <div className="timeline-item">
                  <div className="timeline-time">Yesterday</div>
                  <div className="timeline-badge badge-gray">
                    <span className="material-symbols-outlined" style={{ fontSize: 14 }}>search</span>
                  </div>
                  <div className="timeline-content">
                    <strong>Found 5 roles</strong>
                    <p>Added 5 new roles to your Discovery queue.</p>
                  </div>
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
