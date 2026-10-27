import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Navigate, NavLink, Route, Routes, useLocation } from "react-router-dom";
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
import KenyaJobs from "./pages/KenyaJobs";
import Volunteers from "./pages/Volunteers";
import { useAuth } from "./context/AuthContext";
import { FeedbackModal } from "./FeedbackModal";
import {
  checkAndNotifyDailyDeadlines,
  dismissDeadlineBannerToday,
  getNotificationPermission,
  getUpcomingDeadlines,
  isDeadlineBannerDismissedToday,
  requestNotificationPermission,
  triggerLocalDeadlineNotification,
} from "./deadlineNotifications";
import { fmtDate } from "./OpportunityCard";

// Side nav per the design pack (dashboard_1/job_discovery_1 code.html):
// Home, Discovery, Interview, Memory, Profile — with Material Symbols matching
// the mockups. Interview routes per-job (JobDetail → AI Interview), so the nav
// item simply lands on Jobs. Internships, Programs and Volunteer stay public
// for visitors but open inside this same shell once someone is signed in.
const NAV = [
  { to: "/jobs", label: "Home", icon: "dashboard", end: true, mobile: true },
  { to: "/review", label: "Discovery", icon: "search", mobile: true },
  // Live Kenyan vacancies from work.ke across all sectors and experience levels
  { to: "/kenya-jobs", label: "Kenya Jobs", icon: "work", mobile: true },
  { to: "/internships", label: "Internships & Opportunities", short: "Internships", icon: "school", end: true },
  { to: "/opportunities", label: "Programs", icon: "event", end: true },
  // Crew, mentoring and speaking slots at tech events — rare, and never on a job board
  { to: "/volunteers", label: "Volunteer", icon: "volunteer_activism", end: true },
  { to: "/memory", label: "Memory", icon: "psychology", mobile: true },
  { to: "/profile", label: "Profile", icon: "person" },
] as const;

const MOBILE_DRAWER_PAGES = [
  { to: "/internships", label: "Internships & Opportunities", icon: "school", end: true },
  { to: "/opportunities", label: "Programs", icon: "event", end: true },
  { to: "/volunteers", label: "Volunteer", icon: "volunteer_activism", end: true },
  { to: "/applications", label: "Applications", icon: "work_history", end: true },
  { to: "/sources", label: "Sources Status", icon: "cable", end: true },
  { to: "/profile", label: "Profile & Resume", icon: "person", end: true },
] as const;

const MOBILE_MORE_PATHS = [
  ...NAV.filter((item) => !("mobile" in item && item.mobile)).map((item) => item.to),
  "/applications",
  "/sources",
];

function Dashboard({ page }: { page?: "internships" | "programs" | "kenya-jobs" | "volunteers" }) {
  const { user, logout } = useAuth();
  const location = useLocation();
  const [moreOpen, setMoreOpen] = useState(false);
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
  // Re-evaluates jobs that already have a score, so a relevance fix applies
  // to the existing feed instead of only to postings found from now on.
  const rescore = useMutation({
    mutationFn: () => api.rescore(50),
    onSuccess: () => qc.invalidateQueries(),
  });

  const busy = poll.isPending || score.isPending || rescore.isPending;
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const moreActive = MOBILE_MORE_PATHS.some(
    (path) => location.pathname === path || location.pathname.startsWith(`${path}/`),
  );

  const internships = useQuery({
    queryKey: ["internships"],
    queryFn: api.internships,
    staleTime: 12 * 60 * 60_000,
    refetchOnWindowFocus: false,
  });
  const opportunities = useQuery({
    queryKey: ["opportunities"],
    queryFn: api.opportunities,
    staleTime: 12 * 60 * 60_000,
    refetchOnWindowFocus: false,
  });
  const volunteers = useQuery({
    queryKey: ["volunteers"],
    queryFn: api.volunteers,
    staleTime: 12 * 60 * 60_000,
    refetchOnWindowFocus: false,
  });

  // Volunteer calls close and are gone; they belong in the deadline banner as
  // much as a program application does.
  const allOpportunities = [
    ...(internships.data?.items ?? []),
    ...(opportunities.data?.items ?? []),
    ...(volunteers.data?.items ?? []),
  ];

  const upcomingDeadlines = getUpcomingDeadlines(allOpportunities, 7);
  const [bannerDismissed, setBannerDismissed] = useState(isDeadlineBannerDismissedToday());
  const [notifPerm, setNotifPerm] = useState(getNotificationPermission());

  useEffect(() => {
    if (allOpportunities.length > 0) {
      checkAndNotifyDailyDeadlines(allOpportunities);
    }
  }, [allOpportunities.length]);

  useEffect(() => {
    setMoreOpen(false);
  }, [location.pathname]);

  useEffect(() => {
    if (!moreOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMoreOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => {
      document.body.style.overflow = previousOverflow;
      window.removeEventListener("keydown", onKey);
    };
  }, [moreOpen]);

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
            className={`nav-link${"mobile" in item && item.mobile ? "" : " nav-link-secondary"}`}
          >
            <span className="material-symbols-outlined" aria-hidden="true">{item.icon}</span>
            <span className="nav-label nav-label-full">{item.label}</span>
            <span className="nav-label nav-label-short">{"short" in item ? item.short : item.label}</span>
          </NavLink>
        ))}

        <button
          type="button"
          className="nav-link nav-more"
          onClick={() => setMoreOpen(true)}
          aria-expanded={moreOpen}
          aria-controls="mobile-more-menu"
          aria-current={moreActive ? "page" : undefined}
        >
          <span className="material-symbols-outlined" aria-hidden="true">menu</span>
          <span className="nav-label">More</span>
        </button>

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

      {moreOpen && (
        <div className="mobile-more-backdrop" onClick={() => setMoreOpen(false)}>
          <aside
            id="mobile-more-menu"
            className="mobile-more-panel"
            role="dialog"
            aria-modal="true"
            aria-label="More navigation"
            onClick={(event) => event.stopPropagation()}
          >
            <div className="mobile-more-head">
              <div>
                <div className="mobile-more-eyebrow">ApplyCanary</div>
                <h2 className="mobile-more-title">More</h2>
              </div>
              <button
                type="button"
                className="btn-ghost mobile-more-close"
                onClick={() => setMoreOpen(false)}
                aria-label="Close menu"
                autoFocus
              >
                <span className="material-symbols-outlined" aria-hidden="true">close</span>
              </button>
            </div>

            <div className="mobile-more-user-card">
              <div className="sidebar-user-avatar">{user?.email?.[0]?.toUpperCase() ?? "A"}</div>
              <div className="sidebar-user-info">
                <div className="sidebar-user-name">{user?.email?.split("@")[0] ?? ""}</div>
                <div className="sidebar-user-plan">
                  {user?.is_admin ? "Administrator" : "Free Plan"}
                </div>
              </div>
            </div>

            <nav className="mobile-more-links" aria-label="More pages">
              {MOBILE_DRAWER_PAGES.map((item) => (
                <NavLink
                  key={item.label}
                  to={item.to}
                  end={"end" in item ? item.end : false}
                  className="mobile-more-link"
                >
                  <span className="material-symbols-outlined" aria-hidden="true">{item.icon}</span>
                  <span>{item.label}</span>
                  <span className="material-symbols-outlined mobile-more-arrow" aria-hidden="true">chevron_right</span>
                </NavLink>
              ))}
            </nav>

            <div className="mobile-more-actions">
              <button
                type="button"
                className="btn-ai mobile-more-action-btn"
                onClick={() => {
                  setMoreOpen(false);
                  void score.mutate();
                }}
                disabled={busy}
                title="Score unscored jobs against your resume"
              >
                <span className="material-symbols-outlined" aria-hidden="true">auto_fix_high</span>
                Score Jobs
              </button>
              <button
                type="button"
                className="btn-ghost mobile-more-action-btn"
                onClick={() => {
                  setMoreOpen(false);
                  setFeedbackOpen(true);
                }}
              >
                <span className="material-symbols-outlined" aria-hidden="true">chat</span>
                Feedback &amp; Help
              </button>
              <button
                type="button"
                className="btn-ghost mobile-more-action-btn mobile-more-logout"
                onClick={() => {
                  setMoreOpen(false);
                  void logout();
                }}
              >
                <span className="material-symbols-outlined" aria-hidden="true">logout</span>
                Sign Out
              </button>
            </div>
          </aside>
        </div>
      )}

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
          <button
            onClick={() => rescore.mutate()}
            disabled={busy}
            className="btn-ghost"
            title="Re-score jobs you have already scored, applying the current relevance filters"
          >
            {rescore.isPending ? <span className="spinner" /> : <span className="material-symbols-outlined" aria-hidden="true">restart_alt</span>}
            <span className="topbar-btn-label">Re-score</span>
          </button>
          <button
            type="button"
            onClick={() => setFeedbackOpen(true)}
            className="btn-ghost"
            title="Send feedback or contact admin"
          >
            <span className="material-symbols-outlined" aria-hidden="true">chat</span>
            <span className="topbar-btn-label">Feedback</span>
          </button>
        </header>

        <main id="main" className="content">
          {(() => {
            const nearest = upcomingDeadlines[0];
            if (!nearest || bannerDismissed) return null;
            return (
              <div
                className="banner banner-warn"
                role="status"
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  flexWrap: "wrap",
                  gap: 10,
                }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                  <span className="material-symbols-outlined" style={{ fontSize: 20 }}>alarm</span>
                  <span>
                    <strong>Upcoming deadline:</strong> {nearest.title} ({nearest.org}){" "}
                    {nearest.daysLeft === 0
                      ? "closes today!"
                      : `closes in ${nearest.daysLeft} day${nearest.daysLeft > 1 ? "s" : ""}`}{" "}
                    ({fmtDate(nearest.deadline)}).
                    {upcomingDeadlines.length > 1 &&
                      ` (${upcomingDeadlines.length - 1} more closing this week)`}
                  </span>
                </div>
                <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
                  {notifPerm === "default" && (
                    <button
                      type="button"
                      className="banner-action"
                      onClick={async () => {
                        const res = await requestNotificationPermission();
                        setNotifPerm(res);
                        if (res === "granted") {
                          triggerLocalDeadlineNotification(upcomingDeadlines);
                        }
                      }}
                    >
                      Enable device alerts
                    </button>
                  )}
                  <NavLink
                    to={
                      nearest.kind === "Internship" ? "/internships"
                      : nearest.kind === "Volunteer" ? "/volunteers"
                      : "/opportunities"
                    }
                    className="banner-action"
                  >
                    View details
                  </NavLink>
                  <button
                    type="button"
                    className="btn-ghost"
                    style={{ padding: "2px 6px", minWidth: "auto", height: "auto" }}
                    onClick={() => {
                      dismissDeadlineBannerToday();
                      setBannerDismissed(true);
                    }}
                    title="Dismiss for today"
                    aria-label="Dismiss deadline alert for today"
                  >
                    <span className="material-symbols-outlined" style={{ fontSize: 16 }}>close</span>
                  </button>
                </div>
              </div>
            );
          })()}
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
          ) : page === "volunteers" ? (
            <Volunteers embedded />
          ) : page === "kenya-jobs" ? (
            <KenyaJobs embedded />
          ) : (
            <Routes>
              <Route path="/jobs" element={<Jobs />} />
              <Route path="/job/:id" element={<JobDetail />} />
              <Route path="/job/:id/interview" element={<InterviewStudio />} />
              <Route path="/kenya-jobs" element={<KenyaJobs embedded />} />
              <Route path="/entry-level" element={<Navigate to="/kenya-jobs" replace />} />
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
      <FeedbackModal isOpen={feedbackOpen} onClose={() => setFeedbackOpen(false)} />
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
    {/* Programs stay public; internships and volunteer calls are curated
        account features now, gated by the auth middleware too. All three open
        inside the dashboard shell for signed-in users, with the explicit page
        prop because a descendant Routes would resolve against this path. */}
    <Route path="/opportunities" element={user ? <Dashboard page="programs" /> : <Opportunities />} />
    <Route path="/internships" element={user ? <Dashboard page="internships" /> : <Navigate to="/" replace />} />
    <Route path="/volunteers" element={user ? <Dashboard page="volunteers" /> : <Navigate to="/" replace />} />
    <Route path="/kenya-jobs" element={user ? <Dashboard page="kenya-jobs" /> : <KenyaJobs embedded={false} />} />
    <Route path="/entry-level" element={<Navigate to="/kenya-jobs" replace />} />
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
