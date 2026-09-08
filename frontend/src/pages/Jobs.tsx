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
      <div className="page-intro" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end" }}>
        <div>
          <h2 className="page-title">Job Discovery</h2>
          <p className="page-sub">
            AI-curated matches based on your updated profile.
          </p>
        </div>
        <span className="chip chip-accent" style={{ background: "var(--ac-primary-fixed)", color: "var(--ac-primary)", fontWeight: 600, padding: "6px 12px", borderRadius: 999 }}>
          {data?.counts.total ?? 0} Total Matches
        </span>
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
                        className="btn-ai btn-sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          navigate(`/job/${job.id}`);
                        }}
                      >
                        Tailor Resume
                      </button>
                      <button
                        className="btn-primary btn-sm"
                        onClick={(e) => {
                          e.stopPropagation();
                          if (job.apply_url) window.open(job.apply_url, "_blank");
                          else navigate(`/job/${job.id}`);
                        }}
                      >
                        Apply Now
                      </button>
                    </div>
                  </div>
                </article>
              );
            })}
          </div>

          <aside className="market-insights">
            <div className="card">
              <h3 className="card-title">Market Insights</h3>
              <div className="insight-block">
                <div className="insight-header">
                  <span className="material-symbols-outlined" style={{ color: "var(--ac-primary)" }}>trending_up</span>
                  <strong>Trending Skill</strong>
                </div>
                <p style={{ fontSize: 13, color: "var(--text-dim)", margin: "6px 0 0" }}>
                  <strong>TypeScript &amp; React</strong> are mentioned in 68% of jobs matching your profile.
                </p>
              </div>

              <div className="insight-block" style={{ marginTop: 16 }}>
                <div className="insight-header">
                  <span className="material-symbols-outlined" style={{ color: "var(--ac-secondary)" }}>payments</span>
                  <strong>Salary Range</strong>
                </div>
                <p style={{ fontSize: 13, color: "var(--text-dim)", margin: "6px 0 4px" }}>
                  Current market average for target roles is <strong>$145k</strong>.
                </p>
                <div className="salary-bar-container">
                  <div className="salary-bar-fill" style={{ width: "70%" }} />
                </div>
                <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: "var(--text-faint)", marginTop: 4 }}>
                  <span>$110k</span>
                  <span>$180k</span>
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
