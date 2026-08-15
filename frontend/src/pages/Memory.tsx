import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api } from "../api";
import { Empty, ErrorBox, Loading } from "../components";

const KIND_LABELS: Record<string, string> = {
  interview_summary: "Interview summary",
  coaching_feedback: "Coaching feedback",
  user_context: "User context",
  application_outcome: "Application outcome",
};

export default function Memory() {
  const { data, isPending, error } = useQuery({
    queryKey: ["memory"],
    queryFn: api.memory,
  });

  if (isPending) return <Loading label="Reading agent memory" />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  const sessions = data.sessions.filter((s) => s.status === "finished");
  const last = sessions[0];

  return (
    <>
      <div className="stat-row">
        <Stat label="Interviews" value={data.counts.sessions} />
        <Stat label="Memories stored" value={data.counts.memories} />
        <Stat
          label="Last score"
          value={last?.avg_score != null ? Math.round(last.avg_score) : "—"}
        />
        <Stat
          label="Best score"
          value={sessions.length ? Math.round(Math.max(...sessions.map((s) => s.avg_score ?? 0))) : "—"}
        />
      </div>

      {data.trend.length >= 2 && (
        <div className="card">
          <h3 className="card-title">Improvement trend</h3>
          <div className="trend">
            {data.trend.map((point, i) => (
              <div key={i} className="trend-col" title={`${Math.round(point.score)} on ${point.date ?? ""}`}>
                <div className="trend-value num">{Math.round(point.score)}</div>
                <div
                  className="trend-bar"
                  style={{ height: `${Math.max(6, Math.round(point.score))}%` }}
                />
                <div className="trend-label num">{i + 1}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="card">
        <h3 className="card-title">Agent memory — what the coach knows</h3>
        {data.entries.length === 0 ? (
          <Empty
            title="No memories yet"
            hint="Complete an AI interview and the coach will start remembering you."
          />
        ) : (
          data.entries.map((e) => (
            <div key={e.id} className="memory-entry">
              <div className="memory-kind">
                <span className="chip chip-accent">{KIND_LABELS[e.kind] ?? e.kind}</span>
                {e.created_at && (
                  <span className="muted" style={{ fontSize: 11 }}>{new Date(e.created_at).toLocaleString()}</span>
                )}
              </div>
              <p className="prose" style={{ color: "var(--text)", margin: "4px 0 0" }}>{e.content}</p>
            </div>
          ))
        )}
      </div>

      <div className="card">
        <h3 className="card-title">Interview history</h3>
        {data.sessions.length === 0 ? (
          <Empty
            title="No interviews yet"
            hint={
              <>
                Open a job and hit{" "}
                <Link to="/" className="cell-dim">AI Interview</Link> to start practising.
              </>
            }
          />
        ) : (
          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th>Job</th>
                  <th style={{ width: 90 }}>Score</th>
                  <th style={{ width: 90 }}>Mode</th>
                  <th style={{ width: 130 }}>When</th>
                </tr>
              </thead>
              <tbody>
                {data.sessions.map((s) => (
                  <tr key={s.id} className="row-link">
                    <td>
                      <JobLink jobId={s.job_id} />
                    </td>
                    <td>
                      <span className={`score ${(s.avg_score ?? 0) >= 70 ? "score-strong" : (s.avg_score ?? 0) >= 40 ? "score-mid" : "score-weak"}`}>
                        {s.avg_score != null ? Math.round(s.avg_score) : "—"}
                      </span>
                    </td>
                    <td className="cell-dim">{s.mode === "speech" ? "🎙 voice" : "⌨ typed"}</td>
                    <td className="cell-dim">
                      {s.finished_at ? new Date(s.finished_at).toLocaleString() : "in progress"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

function JobLink({ jobId }: { jobId: number }) {
  const { data } = useQuery({ queryKey: ["job", jobId], queryFn: () => api.job(jobId) });
  if (!data) return <span className="cell-dim">Job #{jobId}</span>;
  return (
    <Link to={`/job/${jobId}`} className="cell-title">
      {data.title} <span className="cell-dim">· {data.company}</span>
    </Link>
  );
}

function Stat({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="stat">
      <div className="stat-value">{value}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}
