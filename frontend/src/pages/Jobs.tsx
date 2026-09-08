import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, type JobFilters } from "../api";
import {
  Chips, Empty, ErrorBox, TableSkeleton, formatSalary, relTime,
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
      <div className="stat-row">
        <Stat label="Total" value={data?.counts.total ?? 0} />
        <Stat label="Scored" value={data?.counts.scored ?? 0} />
        <Stat label="Queued" value={data?.counts.queued ?? 0} />
        <Stat label="Applied" value={data?.counts.applied ?? 0} />
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
          placeholder="Search title or company…"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          aria-label="Search jobs"
        />

        <select
          value={filters.source ?? ""}
          onChange={(e) => set("source", e.target.value)}
          aria-label="Filter by source"
        >
          <option value="">All sources</option>
          {data?.sources.map((s) => <option key={s} value={s}>{s}</option>)}
        </select>

        <select
          value={filters.status ?? ""}
          onChange={(e) => set("status", e.target.value)}
          aria-label="Filter by status"
        >
          <option value="">Any status</option>
          {["new", "scored", "queued", "applied", "skipped", "rejected"].map((s) => (
            <option key={s} value={s}>{s}</option>
          ))}
        </select>

        <select
          value={String(filters.min_score ?? 0)}
          onChange={(e) => set("min_score", Number(e.target.value))}
          aria-label="Minimum score"
        >
          <option value="0">Any score</option>
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
          Remote
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
                className="job-card"
                onClick={() => navigate(`/job/${job.id}`)}
                tabIndex={0}
                role="link"
                onKeyDown={(e) => {
                  if (e.key === "Enter") navigate(`/job/${job.id}`);
                }}
              >
                <div className="job-score-band band-strong" aria-hidden="true">
                  {total !== null ? (
                    <>
                      {total}
                      <span className="job-score-pct">%</span>
                    </>
                  ) : (
                    <span className="material-symbols-outlined">help</span>
                  )}
                </div>
                <div className="job-card-body">
                  <div className="job-card-source">
                    <span className="material-symbols-outlined" aria-hidden="true">rss_feed</span>
                    {job.source}
                  </div>
                  <h3 className="job-card-title">{job.title}</h3>
                  <div className="job-card-company">
                    <strong>{job.company}</strong>
                    <span aria-hidden="true">•</span>
                    <span className="material-symbols-outlined" aria-hidden="true">location_on</span>
                    {job.is_remote ? "Remote" : job.location || "Location not stated"}
                  </div>
                  <div className="job-card-meta">
                    {salary && <span className="chip">{salary}</span>}
                    {job.status !== "new" && <span className="chip">{job.status}</span>}
                    <span className="job-card-age" title={job.posted_at ?? job.first_seen_at}>
                      <span className="material-symbols-outlined" aria-hidden="true">schedule</span>
                      {relTime(job.posted_at ?? job.first_seen_at)} ago
                    </span>
                  </div>
                  {job.score?.missing_keywords.length ? (
                    <Chips items={job.score.missing_keywords} variant="miss" max={4} />
                  ) : null}
                </div>
                <div className="job-card-actions">
                  <span className={`score score-${band}`} title={job.score?.verdict || undefined}>
                    {total !== null ? total : "—"}
                  </span>
                  <button
                    className="btn-ai btn-sm"
                    onClick={(e) => {
                      e.stopPropagation();
                      navigate(`/job/${job.id}`);
                    }}
                  >
                    <span className="material-symbols-outlined" aria-hidden="true">edit_document</span>
                    View
                  </button>
                </div>
              </article>
            );
          })}
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

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
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
