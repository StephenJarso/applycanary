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

// Side nav items matching desktop design mockups (Home, Discovery, Memory, Profile).
// Interviews are reached per-job (JobDetail → AI Interview), not from the nav —
// the earlier duplicate /memory entry made two items highlight at once.
const NAV = [
  { to: "/", label: "Home", icon: "home", end: true },
  { to: "/review", label: "Discovery", icon: "explore" },
  { to: "/memory", label: "Memory", icon: "psychology" },
  { to: "/profile", label: "Profile", icon: "person" },
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

  const busy = poll.isPending || score.isPending;

  return (
    <div className="app">
      <a className="skip-link" href="#main">Skip to content</a>

      <nav className="sidebar" aria-label="Main">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">A</span>
          <div>
            <div className="brand-name">ApplyCanary</div>
            <div className="brand-sub">AI Career Agent</div>
          </div>
        </div>

        {NAV.map((item) => (
          <NavLink
            key={item.label}
            to={item.to}
            end={"end" in item ? item.end : false}
            className="nav-link"
          >
            <span className="material-symbols-outlined" aria-hidden="true">{item.icon}</span>
            <span className="nav-label">{item.label}</span>
          </NavLink>
        ))}

        <div className="sidebar-foot">
          <button
            className="btn-ai sidebar-tailor"
            onClick={() => void score.mutate()}
            disabled={busy}
            title="Score unscored jobs against your resume"
          >
            <span className="material-symbols-outlined" aria-hidden="true">auto_fix_high</span>
            Score Jobs
          </button>
          <div className="sidebar-user">
            <div className="sidebar-user-avatar">{user?.email?.[0]?.toUpperCase() ?? "A"}</div>
            <div className="sidebar-user-info">
              <div className="sidebar-user-name">{user?.email?.split("@")[0] ?? "Alex Smith"}</div>
              <div className="sidebar-user-plan">Free Plan</div>
            </div>
            <button className="btn-ghost logout-button" onClick={() => void logout()} title="Sign out">
              <span className="material-symbols-outlined" aria-hidden="true">logout</span>
            </button>
          </div>
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
