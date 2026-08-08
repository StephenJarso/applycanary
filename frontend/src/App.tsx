import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api";
import Jobs from "./pages/Jobs";
import JobDetail from "./pages/JobDetail";
import Review from "./pages/Review";
import Applications from "./pages/Applications";
import Sources from "./pages/Sources";
import ProfilePage from "./pages/Profile";

const NAV = [
  { to: "/", label: "Jobs", end: true, countKey: "total" },
  { to: "/review", label: "Review", countKey: "queued" },
  { to: "/applications", label: "Applied", countKey: "applied" },
  { to: "/sources", label: "Sources", countKey: null },
  { to: "/profile", label: "Profile", countKey: null },
] as const;

export default function App() {
  const qc = useQueryClient();
  const status = useQuery({ queryKey: ["status"], queryFn: api.status });

  const poll = useMutation({
    mutationFn: api.poll,
    // Everything downstream of ingestion changes, so invalidate broadly rather
    // than trying to predict which queries a poll touched.
    onSuccess: () => qc.invalidateQueries(),
  });
  const score = useMutation({
    mutationFn: api.score,
    onSuccess: () => qc.invalidateQueries(),
  });

  const counts = status.data?.counts ?? {};
  const busy = poll.isPending || score.isPending;

  return (
    <div className="app">
      <a className="skip-link" href="#main">Skip to content</a>

      <nav className="sidebar" aria-label="Main">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">◆</span>
          ApplyCanary
        </div>

        {NAV.map((item) => (
          <NavLink
            key={item.to}
            to={item.to}
            end={"end" in item ? item.end : false}
            className="nav-link"
          >
            {item.label}
            {item.countKey && counts[item.countKey] ? (
              <span className="nav-count">{counts[item.countKey]}</span>
            ) : null}
          </NavLink>
        ))}

        <div className="sidebar-foot">
          {status.data && (
            <div style={{ fontSize: 11, color: "var(--text-faint)", padding: "0 9px" }}>
              <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
                <span
                  className={`dot ${status.data.scheduler_running ? "dot-ok" : "dot-idle"}`}
                />
                {status.data.scheduler_running ? "Scheduler on" : "Scheduler off"}
              </div>
              <div style={{ display: "flex", alignItems: "center", gap: 6, marginTop: 3 }}>
                <span className={`dot ${status.data.auto_submit ? "dot-bad" : "dot-idle"}`} />
                {status.data.auto_submit ? "Auto-submit ON" : "Review mode"}
              </div>
            </div>
          )}
        </div>
      </nav>

      <div className="main">
        <header className="topbar">
          <h1>ApplyCanary</h1>
          <div className="spacer" />
          <button onClick={() => poll.mutate()} disabled={busy} className="btn-ghost">
            {poll.isPending ? <span className="spinner" /> : null}
            Poll sources
          </button>
          <button onClick={() => score.mutate()} disabled={busy} className="btn-ghost">
            {score.isPending ? <span className="spinner" /> : null}
            Score pending
          </button>
        </header>

        <main id="main" className="content">
          {poll.isSuccess && (
            <div className="banner banner-ok" role="status">{poll.data.message}</div>
          )}
          {score.isSuccess && (
            <div className="banner banner-ok" role="status">{score.data.message}</div>
          )}
          {poll.isError && (
            <div className="banner banner-bad" role="alert">{String(poll.error)}</div>
          )}

          {status.data?.warnings.map((w) => (
            <div key={w} className="banner banner-warn" role="status">{w}</div>
          ))}

          <Routes>
            <Route path="/" element={<Jobs />} />
            <Route path="/job/:id" element={<JobDetail />} />
            <Route path="/review" element={<Review />} />
            <Route path="/applications" element={<Applications />} />
            <Route path="/sources" element={<Sources />} />
            <Route path="/profile" element={<ProfilePage />} />
            <Route
              path="*"
              element={<div className="empty"><div className="empty-title">Not found</div></div>}
            />
          </Routes>
        </main>
      </div>
    </div>
  );
}
