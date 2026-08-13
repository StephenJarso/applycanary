import { useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type PublicJob } from "../api";
import { Empty, ErrorBox, Loading, formatSalary, relTime } from "../components";

export default function GuestJobs() {
  const [query, setQuery] = useState("");
  const [filters, setFilters] = useState<{ q?: string; source?: string; remote_only?: boolean }>({});
  const jobs = useQuery({
    queryKey: ["public-jobs", filters],
    queryFn: () => api.publicJobs(filters),
  });

  return (
    <main className="guest-page">
      <header className="guest-header">
        <div>
          <div className="brand"><span className="brand-mark" aria-hidden="true">◆</span> ApplyCanary</div>
          <p className="cell-dim">Browse current openings without creating an account.</p>
        </div>
        <div className="guest-actions">
          <Link className="btn-ghost" to="/login">Sign in</Link>
          <Link className="btn-primary" to="/register">Create account</Link>
        </div>
      </header>

      <section className="guest-toolbar" aria-label="Job filters">
        <form onSubmit={(event) => { event.preventDefault(); setFilters((old) => ({ ...old, q: query || undefined })); }}>
          <input
            type="search"
            placeholder="Search roles or companies"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label="Search roles or companies"
          />
          <button type="submit">Search</button>
        </form>
        <select value={filters.source ?? ""} onChange={(event) => setFilters((old) => ({ ...old, source: event.target.value || undefined }))} aria-label="Filter by source">
          <option value="">All sources</option>
          {jobs.data?.sources.map((source) => <option key={source} value={source}>{source}</option>)}
        </select>
        <label className="check"><input type="checkbox" checked={filters.remote_only ?? false} onChange={(event) => setFilters((old) => ({ ...old, remote_only: event.target.checked || undefined }))} /> Remote</label>
      </section>

      <div className="guest-note">Guest mode is read-only. Your searches and activity are not saved. Create an account to personalize matches, upload a resume, and track applications.</div>
      {jobs.error && <ErrorBox error={jobs.error} />}
      {jobs.isPending && <Loading label="Loading jobs" />}
      {jobs.data && jobs.data.jobs.length === 0 && <Empty title="No public jobs match" hint="Try a broader search or create an account for personalized discovery." />}
      {jobs.data && jobs.data.jobs.length > 0 && (
        <div className="table-wrap">
          <table>
            <caption className="sr-only">Public job postings</caption>
            <thead><tr><th>Role</th><th>Company</th><th>Location</th><th>Salary</th><th>Source</th><th>Age</th><th /></tr></thead>
            <tbody>{jobs.data.jobs.map((job) => <GuestRow key={job.id} job={job} />)}</tbody>
          </table>
        </div>
      )}
    </main>
  );
}

function GuestRow({ job }: { job: PublicJob }) {
  const salary = formatSalary(job.salary_min, job.salary_max, job.salary_currency, job.salary_is_estimate);
  return <tr>
    <td><div className="cell-title">{job.title}</div></td>
    <td>{job.company}</td>
    <td className="cell-dim">{job.is_remote ? <span className="chip chip-accent">remote</span> : job.location || "—"}</td>
    <td className="cell-dim num">{salary ?? "—"}</td>
    <td className="cell-dim">{job.source}</td>
    <td className="cell-dim num">{relTime(job.posted_at ?? job.first_seen_at)}</td>
    <td><a className="btn-ghost" href={job.apply_url} target="_blank" rel="noreferrer">Apply</a></td>
  </tr>;
}
