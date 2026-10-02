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

  return (
    <>
      <div className="page-intro" style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-end", flexWrap: "wrap", gap: 12 }}>
        <div>
          <h2 className="page-title ac-display">Memory &amp; Trends</h2>
          <p className="page-sub">
            <span className="material-symbols-outlined" aria-hidden="true" style={{ color: "var(--ac-secondary)", fontSize: 18 }}>memory</span>
            AI-powered career insights indexed over time
          </p>
        </div>
        <span className="chip chip-accent" style={{ background: "var(--ac-primary-fixed)", color: "var(--ac-primary)", fontWeight: 700, padding: "6px 14px", borderRadius: 999 }}>
          ● POWERED BY COCKROACHDB
        </span>
      </div>

      <div className="grid2" style={{ gap: 16, marginBottom: 16 }}>
        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 6 }}>
            <div>
              <h3 className="ac-headline-md" style={{ margin: 0 }}>Improvement Trend</h3>
              <p style={{ fontSize: 13, color: "var(--text-faint)", margin: "2px 0 0" }}>Mock Interview Scores</p>
            </div>
            <span className="trend-mom-badge">
              <span className="material-symbols-outlined icon-fill" style={{ fontSize: 15 }}>trending_up</span>
              +14% MoM
            </span>
          </div>
          <p style={{ fontSize: 12, color: "var(--text-dim)", margin: "-6px 0 14px" }}>
            Interview composite scores over the last 6 months.
          </p>
          <div className="trend">
            {(data.trend.length >= 2 ? data.trend : [
              { score: 35, date: "Jan" },
              { score: 45, date: "Feb" },
              { score: 60, date: "Mar" },
              { score: 72, date: "Apr" },
              { score: 88, date: "May" }
            ]).map((point, i) => (
              <div key={i} className="trend-col" title={`${Math.round(point.score)} on ${point.date ?? ""}`}>
                <div className="trend-value num">{Math.round(point.score)}</div>
                <div
                  className="trend-bar"
                  style={{ height: `${Math.max(12, Math.round(point.score))}%` }}
                />
                <div className="trend-label num">{point.date ?? i + 1}</div>
              </div>
            ))}
          </div>
        </div>

        <div className="card">
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <span className="material-symbols-outlined" style={{ color: "var(--ac-primary)" }}>notifications_active</span>
            <h3 className="ac-headline-md" style={{ margin: 0 }}>Match Threshold</h3>
          </div>
          <p style={{ fontSize: 12, color: "var(--text-dim)", margin: "0 0 16px" }}>
            Alert me when a job matches my profile above this confidence score.
          </p>
          {/* memory_trends_1: threshold tile — big value above a real slider. */}
          <div className="sensitivity-card">
            <div className="ac-metric-value" style={{ color: "var(--ac-primary)", marginBottom: 8 }}>90%</div>
            <input
              type="range"
              min={50}
              max={100}
              defaultValue={90}
              aria-label="Match threshold"
              style={{ width: "100%" }}
            />
            <div style={{ display: "flex", justifyContent: "space-between", marginTop: 8 }}>
              <span className="ac-label-md" style={{ color: "var(--text-faint)" }}>Loose (50%)</span>
              <span className="ac-label-md" style={{ color: "var(--text-faint)" }}>Strict (100%)</span>
            </div>
          </div>
        </div>
      </div>

      <div className="memory-cards-trio" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 16, marginBottom: 16 }}>
        <div className="card">
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.08em", color: "var(--text-faint)", display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
            <span className="material-symbols-outlined icon-fill" style={{ fontSize: 16, color: "var(--ac-secondary)", background: "var(--ac-canary)", borderRadius: "50%", width: 28, height: 28, display: "grid", placeItems: "center" }}>code</span>
            STRONGEST SKILL
          </div>
          <p className="ac-headline-md" style={{ margin: 0, fontSize: 15, color: "var(--text)" }}>Go / System Design</p>
        </div>

        <div className="card">
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.08em", color: "var(--text-faint)", display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
            <span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ac-on-error-container)", background: "var(--ac-error-container)", borderRadius: "50%", width: 28, height: 28, display: "grid", placeItems: "center" }}>communication</span>
            IMPROVEMENT AREA
          </div>
          <p className="ac-headline-md" style={{ margin: 0, fontSize: 15, color: "var(--text)" }}>Behavioral STAR examples</p>
        </div>

        <div className="card">
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.08em", color: "var(--text-faint)", display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
            <span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ac-on-primary-container)", background: "var(--ac-primary-container)", borderRadius: "50%", width: 28, height: 28, display: "grid", placeItems: "center" }}>speed</span>
            IDEAL PACE
          </div>
          <p className="ac-headline-md" style={{ margin: 0, fontSize: 15, color: "var(--text)" }}>Fast-growing Series B/C</p>
        </div>
      </div>

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14, flexWrap: "wrap", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span className="material-symbols-outlined" style={{ color: "var(--ac-primary)" }}>history_edu</span>
            <h3 className="ac-headline-md" style={{ margin: 0 }}>Remembered Feedback</h3>
          </div>
          <span className="chip" title="Every memory is embedded and indexed for semantic recall">
            <span className="material-symbols-outlined" style={{ fontSize: 12 }}>database</span>
            Vector Indexed
          </span>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn-sm btn-ghost">Filter</button>
            <button className="btn-sm btn-ghost">Export Log</button>
          </div>
        </div>

        {data.entries.length === 0 ? (
          <Empty
            title="No memories yet"
            hint="Complete an AI interview and the coach will start remembering you."
          />
        ) : (
          <div className="semantic-timeline">
            {data.entries.map((e, idx) => (
              <div key={e.id} className="semantic-item">
                <div className="semantic-dot-col">
                  <div className={`semantic-dot ${idx === 0 ? "active" : ""}`} />
                  {idx < data.entries.length - 1 && <div className="semantic-line" />}
                </div>
                <div className="semantic-content-card">
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 8, marginBottom: 6 }}>
                    <span className="chip chip-accent" style={{ fontSize: 10, textTransform: "uppercase" }}>
                      {KIND_LABELS[e.kind] ?? e.kind}
                    </span>
                    {e.created_at && (
                      <span className="muted" style={{ fontSize: 11 }}>{new Date(e.created_at).toLocaleDateString()}</span>
                    )}
                  </div>
                  <p className="prose" style={{ color: "var(--text)", margin: 0, fontSize: 13 }}>
                    "{e.content}"
                  </p>
                  <div style={{ marginTop: 8, display: "flex", alignItems: "center", gap: 6, fontSize: 11, color: "var(--ac-primary)" }}>
                    <span className="material-symbols-outlined" style={{ fontSize: 14 }}>bookmark</span>
                    <span>Indexed in CockroachDB vector store</span>
                  </div>
                </div>
              </div>
            ))}
          </div>
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
                <Link to="/jobs" className="cell-dim">AI Interview</Link> to start practising.
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


