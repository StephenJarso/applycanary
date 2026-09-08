import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate, NavLink, Route, Routes } from "react-router-dom";
import { api } from "./api";
import Jobs from "./pages/Jobs";
import JobDetail from "./pages/JobDetail";
import InterviewStudio from "./pages/InterviewStudio";
import Memory from "./pages/Memory";
import Review from "./pages/Review";
import Applications from "./pages/Applications";
import Sources from "./pages/Sources";
import ProfilePage from "./pages/Profile";
import Login from "./pages/Login";
import Register from "./pages/Register";
import ForgotPassword from "./pages/ForgotPassword";
import ResetPassword from "./pages/ResetPassword";
import VerifyEmail from "./pages/VerifyEmail";
import GuestJobs from "./pages/GuestJobs";
import { useAuth } from "./context/AuthContext";

// Icon names are Material Symbols Outlined, matching the design mockups'
// desktop side nav (dashboard / search / record_voice_over / psychology / person).
const NAV = [
  { to: "/", label: "Jobs", icon: "dashboard", end: true, countKey: "total" },
  { to: "/review", label: "Review", icon: "fact_check", countKey: "queued" },
  { to: "/applications", label: "Applied", icon: "work", countKey: "applied" },
  { to: "/memory", label: "Memory", icon: "psychology", countKey: null },
  { to: "/sources", label: "Sources", icon: "cable", countKey: null },
  { to: "/profile", label: "Profile", icon: "person", countKey: null },
] as const;

function Dashboard() {
  const { user, logout } = useAuth();
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
            <span className="material-symbols-outlined" aria-hidden="true">{item.icon}</span>
            <span className="nav-label">{item.label}</span>
            {item.countKey && counts[item.countKey] ? (
              <span className="nav-count">{counts[item.countKey]}</span>
            ) : null}
          </NavLink>
        ))}

        <div className="sidebar-foot">
          <button className="btn-ghost logout-button" onClick={() => void logout()}>
            Sign out
          </button>
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
          <h1 className="topbar-title">ApplyCanary</h1>
          <div className="spacer" />
          <button onClick={() => poll.mutate()} disabled={busy} className="btn-ghost">
            {poll.isPending ? <span className="spinner" /> : <span className="material-symbols-outlined" aria-hidden="true">sync</span>}
            Poll sources
          </button>
          <button onClick={() => score.mutate()} disabled={busy} className="btn-ghost">
            {score.isPending ? <span className="spinner" /> : <span className="material-symbols-outlined" aria-hidden="true">insights</span>}
            Score pending
          </button>
        </header>

        <main id="main" className="content">
          {user && !user.email_verified && (
            <div className="banner banner-warn" role="status">
              Confirm your email address to secure your account — check your inbox for the link.
            </div>
          )}
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
            <Route path="/job/:id/interview" element={<InterviewStudio />} />
            <Route path="/memory" element={<Memory />} />
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

export default function App() {
  const { user, loading } = useAuth();
  if (loading) return <div className="empty">Loading session…</div>;
  return <Routes>
    <Route path="/login" element={user ? <Navigate to="/" replace /> : <Login />} />
    <Route path="/guest" element={<GuestJobs />} />
    <Route path="/register" element={user ? <Navigate to="/" replace /> : <Register />} />
    {/* Public regardless of session: these are opened from an email, and the
        link must not be swallowed by a redirect to /login. Confirming an
        address is also something a signed-in user does. */}
    <Route path="/forgot-password" element={<ForgotPassword />} />
    <Route path="/reset-password" element={<ResetPassword />} />
    <Route path="/verify-email" element={<VerifyEmail />} />
    <Route path="/verify-email-change" element={<VerifyEmail />} />
    <Route path="/*" element={user ? <Dashboard /> : <Navigate to="/login" replace />} />
  </Routes>;
}
