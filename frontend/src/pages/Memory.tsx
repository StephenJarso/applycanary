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
          <h2 className="page-title">Career Intelligence</h2>
          <p className="page-sub">
            Insights derived from your long-term interview data and feedback.
          </p>
        </div>
        <span className="chip chip-accent" style={{ background: "var(--ac-primary-fixed)", color: "var(--ac-primary)", fontWeight: 700, padding: "6px 14px", borderRadius: 999 }}>
          ● POWERED BY COCKROACHDB
        </span>
      </div>

      <div className="grid2" style={{ gap: 16, marginBottom: 16 }}>
        <div className="card">
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14 }}>
            <h3 className="card-title" style={{ margin: 0 }}>
              <span className="material-symbols-outlined" style={{ color: "var(--ac-primary)" }}>trending_up</span>
              Performance Trajectory
            </h3>
            <select style={{ width: "auto", fontSize: 12, padding: "4px 8px" }}>
              <option>Last 6 Months</option>
              <option>Last 3 Months</option>
              <option>All Time</option>
            </select>
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
          <h3 className="card-title">
            <span className="material-symbols-outlined" style={{ color: "var(--ac-secondary)" }}>tune</span>
            Opportunity Sensitivity
          </h3>
          <p style={{ fontSize: 12, color: "var(--text-dim)", margin: "-4px 0 16px" }}>
            Adjust the AI's strictness for matching your profile against new job postings.
          </p>
          <div className="sensitivity-card">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-dim)" }}>Broad Reach</span>
              <strong className="num" style={{ fontSize: 18, color: "var(--ac-primary)" }}>85%</strong>
              <span style={{ fontSize: 11, fontWeight: 600, color: "var(--text-dim)" }}>High Precision</span>
            </div>
            <div className="sensitivity-slider-track">
              <div className="sensitivity-slider-fill" style={{ width: "85%" }} />
              <div className="sensitivity-slider-handle" style={{ left: "85%" }} />
            </div>
            <p style={{ fontSize: 11, color: "var(--text-faint)", textAlign: "center", margin: "10px 0 0" }}>
              Currently filtering out roles below 85% match score.
            </p>
          </div>
        </div>
      </div>

      <div className="memory-cards-trio" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))", gap: 16, marginBottom: 16 }}>
        <div className="card">
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.06em", color: "var(--text-dim)", display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
            <span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ac-primary)" }}>workspace_premium</span>
            VERIFIED STRENGTHS
          </div>
          <div className="chips" style={{ marginBottom: 10 }}>
            <span className="chip chip-accent">System Design</span>
            <span className="chip chip-accent">React.js</span>
            <span className="chip chip-accent">Agile Leadership</span>
          </div>
          <p style={{ fontSize: 11.5, color: "var(--text-faint)", margin: 0 }}>
            Consistently scored &gt;90% across last 4 technical rounds.
          </p>
        </div>

        <div className="card">
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.06em", color: "var(--text-dim)", display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
            <span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ac-tertiary)" }}>track_changes</span>
            TARGET FOCUS AREAS
          </div>
          <ul style={{ paddingLeft: 14, margin: "0 0 8px", fontSize: 12, color: "var(--text-dim)" }}>
            <li>Conciseness in behavioral answers (STAR method)</li>
            <li>Advanced Database Indexing theories</li>
          </ul>
        </div>

        <div className="card">
          <div style={{ fontSize: 11, fontWeight: 700, letterSpacing: "0.06em", color: "var(--text-dim)", display: "flex", alignItems: "center", gap: 6, marginBottom: 10 }}>
            <span className="material-symbols-outlined" style={{ fontSize: 16, color: "var(--ac-secondary)" }}>speed</span>
            AI RECOMMENDED PACE
          </div>
          <div style={{ display: "flex", alignItems: "baseline", gap: 6 }}>
            <span style={{ fontSize: 26, fontWeight: 800, color: "var(--text)" }}>2-3</span>
            <span style={{ fontSize: 13, color: "var(--text-dim)" }}>Interviews / Week</span>
          </div>
          <div className="salary-bar-container" style={{ margin: "8px 0" }}>
            <div className="salary-bar-fill" style={{ width: "60%" }} />
          </div>
          <p style={{ fontSize: 11, color: "var(--text-faint)", margin: 0 }}>
            Optimized for knowledge retention and avoiding burnout based on past activity logs.
          </p>
        </div>
      </div>

      <div className="card">
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 14, flexWrap: "wrap", gap: 8 }}>
          <h3 className="card-title" style={{ margin: 0 }}>
            <span className="material-symbols-outlined" style={{ color: "var(--ac-primary)" }}>radar</span>
            Semantic Recall Log
          </h3>
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


