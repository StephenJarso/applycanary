import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api, type JobFilters } from "../api";
import {
  Chips, Empty, ErrorBox, ScoreBadge, TableSkeleton, formatSalary, relTime,
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
        <div className="table-wrap">
          <table>
            <caption className="sr-only">
              Job postings, {data.jobs.length} shown of {data.total}
            </caption>
            <thead>
              <tr>
                <th scope="col" style={{ width: 52 }}>Score</th>
                <th scope="col">Role</th>
                <th scope="col" style={{ width: 150 }}>Company</th>
                <th scope="col" style={{ width: 130 }}>Location</th>
                <th scope="col" style={{ width: 105 }}>Salary</th>
                <th scope="col" style={{ width: 90 }}>Source</th>
                <th scope="col" style={{ width: 56 }}>Age</th>
              </tr>
            </thead>
            <tbody>
              {data.jobs.map((job) => {
                const salary = formatSalary(
                  job.salary_min, job.salary_max, job.salary_currency, job.salary_is_estimate,
                );
                return (
                  <tr
                    key={job.id}
                    className="row-link"
                    onClick={() => navigate(`/job/${job.id}`)}
                    tabIndex={0}
                    onKeyDown={(e) => {
                      if (e.key === "Enter") navigate(`/job/${job.id}`);
                    }}
                  >
                    <td><ScoreBadge score={job.score} /></td>
                    <td>
                      <div className="cell-title">{job.title}</div>
                      {job.score?.missing_keywords.length ? (
                        <Chips items={job.score.missing_keywords} variant="miss" max={4} />
                      ) : null}
                    </td>
                    <td>{job.company}</td>
                    <td className="cell-dim">
                      {job.is_remote ? <span className="chip chip-accent">remote</span> : job.location || "—"}
                    </td>
                    <td className="cell-dim num">{salary ?? "—"}</td>
                    <td className="cell-dim">{job.source}</td>
                    <td className="cell-dim num" title={job.posted_at ?? job.first_seen_at}>
                      {relTime(job.posted_at ?? job.first_seen_at)}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
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
