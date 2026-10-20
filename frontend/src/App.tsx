import { useEffect, useRef } from "react";
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
import ForgotPassword from "./pages/ForgotPassword";
import ResetPassword from "./pages/ResetPassword";
import VerifyEmail from "./pages/VerifyEmail";
import Landing from "./pages/Landing";
import Opportunities from "./pages/Opportunities";
import Internships from "./pages/Internships";
import { useAuth } from "./context/AuthContext";

// Side nav per the design pack (dashboard_1/job_discovery_1 code.html):
// Home, Discovery, Interview, Memory, Profile — with Material Symbols matching
// the mockups. Interview routes per-job (JobDetail → AI Interview), so the nav
// item simply lands on Jobs. Internships and Programs stay public for visitors
// but open inside this same shell once someone is signed in.
const NAV = [
  { to: "/jobs", label: "Home", icon: "dashboard", end: true },
  { to: "/review", label: "Discovery", icon: "search" },
  { to: "/internships", label: "Internships & Opportunities", short: "Internships", icon: "school", end: true },
  { to: "/opportunities", label: "Programs", icon: "event", end: true },
  { to: "/memory", label: "Memory", icon: "psychology" },
  { to: "/profile", label: "Profile", icon: "person" },
] as const;

function Dashboard({ page }: { page?: "internships" | "programs" }) {
  const { user, logout } = useAuth();
  const qc = useQueryClient();
  const status = useQuery({
    queryKey: ["status"],
    queryFn: api.status,
    // A manual poll runs in the background; follow it until it finishes.
    refetchInterval: (query) => (query.state.data?.poll_running ? 3000 : false),
  });
  const wasPolling = useRef(false);

  useEffect(() => {
    if (status.data?.poll_running) {
      wasPolling.current = true;
      return;
    }
    if (wasPolling.current) {
      wasPolling.current = false;
      // A finished poll touched ingestion, scoring and counts at once.
      void qc.invalidateQueries();
    }
  }, [status.data?.poll_running, qc]);

  const poll = useMutation({
    mutationFn: api.poll,
    // The request returns as soon as the poll starts; keep status fresh so the
    // banner/refresh above sees the transition to running and then finished.
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["status"] }),
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

        <div className="nav-eyebrow">Navigation</div>

        {NAV.map((item) => (
          <NavLink
            key={item.label}
            to={item.to}
            end={"end" in item ? item.end : false}
            className="nav-link"
          >
            <span className="material-symbols-outlined" aria-hidden="true">{item.icon}</span>
            <span className="nav-label nav-label-full">{item.label}</span>
            <span className="nav-label nav-label-short">{"short" in item ? item.short : item.label}</span>
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
              <div className="sidebar-user-name">{user?.email?.split("@")[0] ?? ""}</div>
              <div className="sidebar-user-plan">
                {user?.is_admin ? "Administrator" : "Free Plan"}
              </div>
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
            <span className="topbar-btn-label">Poll sources</span>
          </button>
          <button onClick={() => score.mutate()} disabled={busy} className="btn-ghost">
            {score.isPending ? <span className="spinner" /> : <span className="material-symbols-outlined" aria-hidden="true">insights</span>}
            <span className="topbar-btn-label">Score pending</span>
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
          {status.data?.poll_running && (
            <div className="banner banner-ok" role="status">
              {status.data.poll_message || "Polling sources…"} New postings appear as each board finishes.
            </div>
          )}
          {score.isSuccess && (
            <div className="banner banner-ok" role="status">{score.data.message}</div>
          )}
          {poll.isError && (
            <div className="banner banner-bad" role="alert">{String(poll.error)}</div>
          )}

          {status.data && !status.data.scheduler_running && (
            <div className="banner banner-warn" role="status">
              Automatic polling is paused. Set ENABLE_SCHEDULER=true on Render
              and redeploy so new postings arrive without clicking Poll sources.
            </div>
          )}

          {status.data?.warnings.map((w) => (
            <div key={w} className="banner banner-warn" role="status">{w}</div>
          ))}

          {page === "internships" ? (
            <Internships embedded />
          ) : page === "programs" ? (
            <Opportunities embedded />
          ) : (
            <Routes>
              <Route path="/jobs" element={<Jobs />} />
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
          )}
        </main>
      </div>
    </div>
  );
}

export default function App() {
  const { user, loading } = useAuth();
  if (loading) return <div className="empty">Loading session…</div>;
  return <Routes>
    {/* Signed-out visitors get the marketing page at /, not a login wall.
        Login and Register are now inline panels of that page (mode chosen via
        ?mode=); the standalone pages keep their routes as redirects so old
        links, bookmarks and emailed tokens keep landing on the same spot. */}
    <Route path="/" element={user ? <Navigate to="/jobs" replace /> : <Landing />} />
    <Route path="/login" element={<Navigate to="/?mode=login" replace />} />
    <Route path="/register" element={<Navigate to="/?mode=register" replace />} />
    {/* Programs stay public; internships are an account feature now. Both open
        inside the dashboard shell for signed-in users, with the explicit page
        prop because a descendant Routes would resolve against this path. */}
    <Route path="/opportunities" element={user ? <Dashboard page="programs" /> : <Opportunities />} />
    <Route path="/internships" element={user ? <Dashboard page="internships" /> : <Navigate to="/" replace />} />
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
