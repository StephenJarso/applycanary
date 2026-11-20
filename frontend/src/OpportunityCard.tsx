import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  api,
  generateSop,
  tailorOpportunityResume,
  type Opportunity,
  type TailorOpportunityOut,
} from "./api";
import { useAuth } from "./context/AuthContext";
import { useOpportunityTracking } from "./useOpportunityTracking";

/** Short labels for the tracking control on a card. */
const TRACK_LABELS: Record<string, string> = {
  applied: "Applied",
  responded: "Replied",
  interview: "Interview",
  offer: "Offer",
  rejected: "Rejected",
  ghosted: "Ghosted",
};

const DAY_MS = 86_400_000;
const DATE_ONLY = /^\d{4}-\d{2}-\d{2}$/;

/**
 * A published calendar date, read in the reader's own day.
 *
 * Opportunity dates are days ("2026-12-02"), not instants: `new Date(iso)`
 * parses a bare date as UTC midnight, so it displays as the previous day for
 * anyone west of Greenwich. Building it from the parts keeps the publisher's
 * date intact. Timestamps (work.ke closing times, event starts) stay instants.
 */
export const parseOpportunityDate = (iso: string) => {
  if (DATE_ONLY.test(iso)) {
    return new Date(Number(iso.slice(0, 4)), Number(iso.slice(5, 7)) - 1, Number(iso.slice(8, 10)));
  }
  return new Date(iso);
};

const startOfLocalDay = (d: Date) =>
  new Date(d.getFullYear(), d.getMonth(), d.getDate());

/** Whole calendar days left: the deadline day itself is 0, all day long. */
export const daysUntil = (iso: string) => {
  const target = parseOpportunityDate(iso);
  if (Number.isNaN(target.getTime())) return Number.NaN;
  return Math.round(
    (startOfLocalDay(target).getTime() - startOfLocalDay(new Date()).getTime()) / DAY_MS,
  );
};

export const fmtDate = (iso: string) =>
  parseOpportunityDate(iso).toLocaleDateString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
  });

/** Status shown in the box at the bottom of each card. */
export function opportunityStatus(o: Opportunity): string {
  if (o.kind === "Event") return o.date && daysUntil(o.date) < 0 ? "Closed" : "Upcoming event";
  if (o.status) {
    return o.status === "closed" ? "Closed"
      : o.status === "opening_soon" ? "Opening soon"
      : o.status === "open" ? "Open now"
      : "Rolling";
  }
  if (o.closes) {
    if (o.expired ?? daysUntil(o.closes) < 0) return "Closed";
    return !o.opens || daysUntil(o.opens) <= 0 ? "Open now" : "Opening soon";
  }
  if (o.date) return daysUntil(o.date) < 0 ? "Closed" : "Upcoming event";
  return "Rolling";
}

/** Dated entries sort first, then rolling ones, and closed ones last. */
export const opportunityDeadline = (o: Opportunity) => {
  if (o.expired) return 9e15;
  return o.closes ? new Date(o.closes).getTime()
    : o.date ? new Date(o.date).getTime()
    : 9e15;
};

const ELIGIBILITY_LABELS: Record<string, string> = {
  kenya_resident: "Kenya resident",
  age_18_35: "Age 18–35",
  no_degree_required: "No degree needed",
  student_only: "Students only",
  students: "Students",
  graduates: "Graduates",
  recent_grads: "Recent graduates",
  self_taught_ok: "Self-taught welcome",
  women_and_underrepresented: "Women & underrepresented",
  underrepresented: "Underrepresented groups",
  beginners: "Beginners",
  africa_only: "Africa only",
  global: "Open worldwide",
  open_to_all: "Open to all",
};

/** Actionable qualification tips and common rejection reasons. */
export function getOpportunityTips(item: Opportunity): { tips: string[]; avoid: string[]; focus: string[] } {
  const t = `${item.title} ${item.org} ${item.desc}`.toLowerCase();

  if (t.includes("hennge")) {
    return {
      tips: [
        "Pass 100% of the online coding challenge: Test empty inputs, maximum boundaries, and malformed data locally before submitting. HENNGE's automated grader disqualifies submissions with any failed hidden test cases.",
        "Spotlight working backend code: Ensure your GitHub has at least one production-grade Go or Python project with a Dockerfile, automated tests, and clear README architecture diagram.",
        "Specific Tokyo motivation: In your application essays, explain why HENNGE One cloud security and relocating to Shibuya, Tokyo matches your career trajectory. Avoid generic answers.",
        "Technical interview readiness: Review asynchronous concurrency (goroutines/channels in Go, asyncio in Python), RESTful HTTP status contracts, and basic Linux systems administration.",
      ],
      avoid: [
        "Submitting the coding challenge without local unit testing across edge cases.",
        "Using generic AI-generated answers for the Japan motivation questions.",
        "Having an inactive GitHub profile with only tutorial repos.",
      ],
      focus: ["Go / Python backend engineering", "API design & cloud security", "Test coverage & boundary conditions"],
    };
  }

  if (t.includes("outreachy")) {
    return {
      tips: [
        "Dominate ONE project on Day 1: Do not split contributions across multiple projects. Choose a single organization matching your primary language (Python, Go, or web).",
        "Engage immediately in community chat: Introduce yourself on Zulip, Matrix, or Slack on Day 1 and follow the developer installation instructions.",
        "Submit your first PR within Week 1: Start with beginner-friendly documentation or unit test PRs to get mentor trust early.",
        "CRITICAL — Record every contribution on the Outreachy portal: Mentors and selection committees ONLY evaluate work logged on the Outreachy dashboard. Record every merged PR, reviewed issue, and commit before the period closes.",
        "Commitment & Authenticity: Confirm 40 hours/week availability. In your initial application essays, write authentic personal stories about obstacles overcome.",
      ],
      avoid: [
        "Spreading effort across 4 different organizations without substantial merged PRs in any.",
        "Waiting until the last week of the contribution period to submit your first PR.",
        "Failing to record your contributions on the Outreachy portal (the #1 reason applicants get rejected).",
      ],
      focus: ["Git / GitHub workflow", "Daily mentor communication in Zulip/Matrix", "Portal contribution logging"],
    };
  }

  if (t.includes("canonical")) {
    return {
      tips: [
        "Highlight Linux and Ubuntu depth: comfort with bash scripting, systemd, package management, and container runtimes.",
        "Demonstrate clean, idiomatic Go or Python code on GitHub with automated GitHub Actions CI.",
        "Prepare for Canonical's standardized high-school and university math, logic, and verbal assessment tests.",
      ],
      avoid: ["Claiming Linux administration without hands-on terminal and scripting examples."],
      focus: ["Ubuntu / Linux internals", "Go / Python systems engineering", "Logic & mathematical assessments"],
    };
  }

  if (t.includes("chipper") || t.includes("moniepoint") || t.includes("safari") || t.includes("africas talking") || t.includes("africa's talking")) {
    return {
      tips: [
        "Demonstrate hands-on API integration experience: payment webhooks, M-PESA Daraja STK push, or SMS/USSD services.",
        "Emphasize distributed systems fundamentals: idempotency keys, message queues, and high-availability database queries.",
        "Prepare for technical aptitude and structured behavioral interviews focusing on customer impact.",
      ],
      avoid: ["Treating fintech APIs as black boxes without addressing failure recovery and idempotency."],
      focus: ["Financial APIs & webhooks", "Relational database performance", "Asynchronous messaging & queues"],
    };
  }

  if (t.includes("summer of code") || t.includes("gsoc")) {
    return {
      tips: [
        "Engage with the organization in February/March before the official application window opens.",
        "Submit at least 1-2 merged PRs fixing good-first-issues before writing your proposal.",
        "Draft your project proposal using the organization's official template and share a draft with mentors early for feedback.",
        "Include a detailed weekly schedule with concrete deliverables and milestones in your proposal.",
      ],
      avoid: [
        "Writing a proposal without discussing it with the community mentors first.",
        "Submitting at the last minute without mentor review.",
      ],
      focus: ["Open-source contributions", "Clear timeline & milestone estimation", "Community chat participation"],
    };
  }

  if (t.includes("mest")) {
    return {
      tips: [
        "Demonstrate practical software development experience (full-stack, mobile, or AI projects on GitHub).",
        "Formulate a clear technology startup idea or problem statement relevant to the African continent.",
        "Prepare for the behavioral and aptitude interview: show leadership, adaptability, and cross-cultural teamwork.",
      ],
      avoid: ["Claiming theoretical knowledge without working GitHub code or live demos."],
      focus: ["Full-stack / AI engineering", "African tech problem solving", "Entrepreneurial mindset"],
    };
  }

  if (t.includes("andela")) {
    return {
      tips: [
        "Prepare for automated algorithmic assessments (data structures, problem solving, code efficiency).",
        "Highlight your experience with modern frameworks, testing, and continuous integration.",
        "Emphasize professional communication and remote collaboration readiness.",
      ],
      avoid: ["Weak GitHub history or repositories with zero commit documentation."],
      focus: ["Modern software engineering", "Async remote communication", "Algorithmic problem solving"],
    };
  }

  if (item.kind === "Volunteer") {
    return {
      tips: [
        "Express genuine interest in the specific community or event mission in your application.",
        "Confirm your exact availability for shift days and rehearsals/orientations.",
        "Highlight practical skills: attendee registration, room moderation, AV support, or workshop mentoring.",
      ],
      avoid: ["Ghosting or canceling after being assigned a shift or role."],
      focus: ["Reliability & punctual attendance", "Community enthusiasm", "Team coordination"],
    };
  }

  if (item.kind === "Internship" || item.kind === "Apprenticeship") {
    return {
      tips: [
        "Emphasize your willingness and capacity to learn quickly with concrete project examples.",
        "Highlight 2-3 personal or open-source projects where you solved real problems.",
        "Tailor your CV to mention the exact core technologies mentioned in this posting.",
      ],
      avoid: ["Inflating experience rank beyond entry/learning level."],
      focus: ["Fundamental coding & debugging", "Curiosity & fast learning", "Practical personal projects"],
    };
  }

  return {
    tips: [
      "Review the stated eligibility criteria and application deadlines carefully.",
      "Submit a tailored statement of purpose connecting your background directly to this program's mission.",
      "Submit at least 3-5 days before the closing date to avoid portal traffic crashes.",
    ],
    avoid: ["Submitting a generic one-size-fits-all cover letter."],
    focus: ["Relevance to program mission", "Concrete project evidence", "Clear motivation"],
  };
}

export function Funding({ item }: { item: Opportunity }) {
  const badges: string[] = [];
  if (item.stipend_amount) {
    const period = item.stipend_period === "month" ? "/month"
      : item.stipend_period === "year" ? "/year" : " total";
    badges.push(
      `Stipend ${item.stipend_currency ? `${item.stipend_currency} ` : ""}${item.stipend_amount.toLocaleString()}${period}`,
    );
  }
  if (item.accommodation === "included") badges.push("Accommodation included");
  if (item.airfare_covered) badges.push("Airfare covered");
  if (item.relocation_support) badges.push("Relocation support");
  if (item.remote_friendly) badges.push("Remote-friendly");
  if (badges.length === 0) return null;
  return (
    <div className="opp-funding">
      {badges.map((badge) => (
        <span key={badge} className="opp-funding-badge">{badge}</span>
      ))}
    </div>
  );
}

export function Eligibility({ item }: { item: Opportunity }) {
  const labels = (item.eligibility ?? [])
    .map((key) => ELIGIBILITY_LABELS[key])
    .filter(Boolean);
  if (labels.length === 0) return null;
  return <p className="opp-eligibility">Eligibility: {labels.join(" · ")}</p>;
}

export function Steps({ item }: { item: Opportunity }) {
  if (!item.steps) return null;
  return (
    <ol className="opp-steps">
      {item.steps.map((step) => <li key={step}>{step}</li>)}
    </ol>
  );
}

export function Timeline({ item }: { item: Opportunity }) {
  if (item.kind === "Event") {
    return (
      <>
        {item.date ? (
          <p>
            📅 Register before <b>{fmtDate(item.date)}</b> ·{" "}
            {daysUntil(item.date) >= 0
              ? `in ${daysUntil(item.date)} day${daysUntil(item.date) === 1 ? "" : "s"}`
              : "passed"}
          </p>
        ) : (
          <p>📅 Check the calendar for new dates</p>
        )}
        <Steps item={item} />
      </>
    );
  }

  if (item.closes) {
    const left = item.expired ? -1 : daysUntil(item.closes);
    const opens = item.opens ?? "";
    const opening = opens ? daysUntil(opens) : 0;
    if (left < 0) {
      return (
        <>
          <p>
            🔒 <b>Applications closed</b> · Deadline <b>{fmtDate(item.closes)}</b>
          </p>
          <Steps item={item} />
        </>
      );
    }
    return (
      <>
        <p>
          {opening > 0 ? (
            <>
              ⏳ Opens <b>{fmtDate(opens)}</b> (in {opening} day{opening === 1 ? "" : "s"})
            </>
          ) : left <= 7 && left >= 0 ? (
            <>🚨 <b>Closing soon</b></>
          ) : (
            <>🟢 Open now</>
          )}{" "}
          · Deadline <b>{fmtDate(item.closes)}</b>
          {left >= 0 && (
            <> ({left === 0 ? "closes today!" : `${left} day${left === 1 ? "" : "s"} left`})</>
          )}
        </p>
        <Steps item={item} />
      </>
    );
  }

  if (item.date) {
    const left = daysUntil(item.date);
    return (
      <>
        <p>
          📅 {left >= 0 ? (
            <>
              Happens <b>{fmtDate(item.date)}</b> · in {left} day{left === 1 ? "" : "s"}
            </>
          ) : (
            <>
              Was on <b>{fmtDate(item.date)}</b>
            </>
          )}
        </p>
        <Steps item={item} />
      </>
    );
  }

  return (
    <>
      <p>🔁 Rolling / cohort-based — apply any time or when announced</p>
      <Steps item={item} />
    </>
  );
}

interface OpportunityCardProps {
  item: Opportunity;
  isWatched?: boolean;
  onToggleWatch?: (
    url: string,
    title: string,
    org: string,
    kind: string,
    deadline: string | null,
    opens: string | null,
    currentlyWatched: boolean,
  ) => void;
}

/** Shared card for events, programs, internships and volunteerships. Expands on click. */
export function OpportunityCard({
  item,
  isWatched = false,
  onToggleWatch,
}: OpportunityCardProps) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const deadline =
    item.closes ??
    (item.kind === "Event" || item.kind === "Hackathon" ? item.date : null) ??
    null;

  const tracking = useOpportunityTracking();
  const stage = tracking.stageFor(item.url);

  // Card click navigates to detail page — expand state kept for compatibility
  const expanded = false;
  const [sopOpen, setSopOpen] = useState(false);
  const [sopLoading, setSopLoading] = useState(false);
  const [sopText, setSopText] = useState("");
  const [sopCopied, setSopCopied] = useState(false);
  const [sopError, setSopError] = useState("");

  const [tailorOpen, setTailorOpen] = useState(false);
  const [tailorLoading, setTailorLoading] = useState(false);
  const [tailorResult, setTailorResult] = useState<TailorOpportunityOut | null>(null);
  const [tailorCopied, setTailorCopied] = useState(false);
  const [tailorError, setTailorError] = useState("");

  const { data: profile } = useQuery({
    queryKey: ["profile"],
    queryFn: api.profile,
    enabled: !!user,
    staleTime: 5 * 60_000,
  });

  const tips = getOpportunityTips(item);

  // Compute matched skills between profile and this opportunity
  const matchedSkills = (profile?.skills ?? []).filter((skill) => {
    const hay = `${item.title} ${item.desc} ${item.tags.join(" ")}`.toLowerCase();
    return hay.includes(skill.toLowerCase());
  });

  const handleGenerateSop = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (sopText) {
      setSopOpen((o) => !o);
      return;
    }
    setSopLoading(true);
    setSopOpen(true);
    setSopError("");
    try {
      const res = await generateSop({
        title: item.title,
        org: item.org,
        description: item.desc || `${item.title} by ${item.org}`,
      });
      if (res.ok && res.text) {
        setSopText(res.text);
      } else {
        setSopError(res.error || "Failed to generate SOP.");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to generate SOP.";
      setSopError(msg);
    } finally {
      setSopLoading(false);
    }
  };

  const handleTailorResume = async (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (tailorResult) {
      setTailorOpen((o) => !o);
      return;
    }
    setTailorLoading(true);
    setTailorOpen(true);
    setTailorError("");
    try {
      const res = await tailorOpportunityResume({
        title: item.title,
        org: item.org,
        kind: item.kind,
        description: item.desc || `${item.title} by ${item.org}`,
        tags: item.tags,
      });
      if (res.ok && res.tailored_resume) {
        setTailorResult(res);
      } else {
        setTailorError(res.error || "Failed to tailor resume.");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Failed to tailor resume.";
      setTailorError(msg);
    } finally {
      setTailorLoading(false);
    }
  };

  return (
    <div
      className="opp-card"
      onClick={() => navigate("/opportunity/detail", { state: { opportunity: item } })}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          navigate("/opportunity/detail", { state: { opportunity: item } });
        }
      }}
      style={{ cursor: "pointer", position: "relative" }}
    >
      <div className="opp-card-top">
        <span
          className={
            "opp-kind" +
            (item.kind === "Paid program" ? " opp-kind-program"
              : item.kind === "Apprenticeship" ? " opp-kind-apprentice"
              : item.kind === "Internship" ? " opp-kind-intern"
              : item.kind === "Volunteer" ? " opp-kind-volunteer"
              : item.kind === "Hackathon" ? " opp-kind-hackathon" : "")
          }
        >
          {item.kind}
        </span>
        {item.expired && (
          <span className="opp-source" style={{ fontWeight: 600 }}>
            Closed
          </span>
        )}
        <span className="opp-source">via {item.source}</span>
        {onToggleWatch && (
          <button
            type="button"
            className={`opp-watch-btn${isWatched ? " opp-watch-active" : ""}`}
            title={isWatched ? "Remove deadline alert" : "Get notified 7 days before the window opens or closes"}
            aria-label={isWatched ? "Remove deadline alert" : "Notify me about this deadline"}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onToggleWatch(item.url, item.title, item.org, item.kind, deadline, item.opens ?? null, isWatched);
            }}
          >
            {isWatched ? "🔔" : "🔕"}
          </button>
        )}
        {tracking.signedIn && (
          <span className="opp-track">
            {stage && (
              <span className="opp-watch-btn opp-watch-active" title={`Tracked: ${stage}`}>
                {TRACK_LABELS[stage] ?? stage}
              </span>
            )}
            {stage === "applied" ? (
              <button
                type="button"
                className="opp-watch-btn"
                title="They replied"
                aria-label="Mark as replied"
                disabled={tracking.busy}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  tracking.mark(item, "heard_back");
                }}
              >
                Heard back
              </button>
            ) : !stage ? (
              <button
                type="button"
                className="opp-watch-btn"
                title="Mark this application as sent"
                aria-label="Mark as applied"
                disabled={tracking.busy}
                onClick={(e) => {
                  e.preventDefault();
                  e.stopPropagation();
                  tracking.mark(item, "applied");
                }}
              >
                ✓ Applied
              </button>
            ) : null}
          </span>
        )}
      </div>

      {item.kind === "Hackathon" && (
        <div className="opp-badges" aria-label="Prize and travel coverage">
          {item.cash_prize && (
            <span className="opp-badge opp-badge-cash">💰 Cash prize</span>
          )}
          {item.city === "Online" ? (
            <span className="opp-badge">💻 Online · no travel needed</span>
          ) : (
            <>
              <span
                className={`opp-badge${item.airfare_covered ? " opp-badge-yes" : ""}`}
              >
                ✈️ {item.airfare_covered ? "Travel covered" : "Travel not covered"}
              </span>
              <span
                className={`opp-badge${item.accommodation === "included" ? " opp-badge-yes" : ""}`}
              >
                🏨 {item.accommodation === "included"
                  ? "Accommodation covered"
                  : "Accommodation not covered"}
              </span>
            </>
          )}
        </div>
      )}

      <div>
        <h3>{item.title}</h3>
        <p className="opp-meta">{item.org} · {item.city} · {item.when}</p>
      </div>

      {item.desc && <p className="opp-desc">{item.desc}</p>}
      {item.stipend && <p className="opp-stipend">💰 {item.stipend}</p>}
      <Funding item={item} />
      <Eligibility item={item} />

      <div className="opp-timeline">
        <Timeline item={item} />
      </div>

      <div className="opp-tags">
        {item.tags.map((tag) => (
          <span key={tag} className="opp-tag">{tag}</span>
        ))}
      </div>

      {/* Expand/Collapse prompt banner */}
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          padding: "6px 8px",
          background: "var(--ac-surface-container, rgba(0,0,0,0.03))",
          borderRadius: "6px",
          fontSize: "12px",
          color: "var(--ac-primary, #436436)",
          fontWeight: 600,
          marginTop: "4px",
        }}
      >
        <span>
          {expanded
            ? ""
            : "▶ Tap for tips, what to do to qualify & tailored resume"}
        </span>
        <span style={{ fontSize: "11px", fontWeight: 400, color: "var(--text-dim)" }}>
          {expanded ? "Details open" : "Expand"}
        </span>
      </div>

      {/* Expanded section with qualification tips, resume tailoring, and SOP */}
      {expanded && (
        <div
          className="opp-expanded-details"
          onClick={(e) => e.stopPropagation()}
          style={{
            marginTop: "12px",
            display: "flex",
            flexDirection: "column",
            gap: "12px",
            borderTop: "1px solid var(--border)",
            paddingTop: "12px",
          }}
        >
          {/* Action Row */}
          <div
            style={{
              display: "flex",
              gap: "8px",
              flexWrap: "wrap",
              alignItems: "center",
              justifyContent: "space-between",
            }}
          >
            <div style={{ display: "flex", gap: "8px", flexWrap: "wrap" }}>
              {deadline && (
                <a
                  href={`/api/opportunities/calendar?url=${encodeURIComponent(item.url)}&title=${encodeURIComponent(item.title)}&org=${encodeURIComponent(item.org)}&deadline=${encodeURIComponent(deadline)}`}
                  download
                  className="btn btn-sm btn-ghost"
                  title="Download .ics to add to your calendar"
                  onClick={(e) => e.stopPropagation()}
                  style={{ fontSize: "12px", padding: "4px 10px", textDecoration: "none" }}
                >
                  📅 Add to Calendar
                </a>
              )}
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                title="Generate tailored Statement of Purpose / Motivation Letter"
                onClick={handleGenerateSop}
                disabled={sopLoading}
                style={{ fontSize: "12px", padding: "4px 10px" }}
              >
                ✍ {sopLoading ? "Generating SOP..." : sopText ? (sopOpen ? "Hide Statement of Purpose" : "Show Statement of Purpose") : "Generate Statement of Purpose"}
              </button>
              <button
                type="button"
                className="btn btn-sm btn-ghost"
                title="Generate tailored resume and application strategy for this opportunity"
                onClick={handleTailorResume}
                disabled={tailorLoading}
                style={{ fontSize: "12px", padding: "4px 10px" }}
              >
                📄 {tailorLoading ? "Tailoring CV..." : tailorResult ? (tailorOpen ? "Hide Tailored CV" : "Show Tailored CV") : "Generate Tailored Resume"}
              </button>
            </div>

            <a
              href={item.url}
              target="_blank"
              rel="noreferrer"
              className="btn btn-sm btn-primary"
              style={{
                fontSize: "12px",
                padding: "4px 12px",
                textDecoration: "none",
                fontWeight: 600,
              }}
              onClick={(e) => e.stopPropagation()}
            >
              Open Official Advert / Apply ↗
            </a>
          </div>

          {/* Section 1: How to Qualify & Get Considered */}
          <div
            style={{
              background: "var(--ac-surface-container, rgba(0,0,0,0.03))",
              padding: "12px",
              borderRadius: "8px",
              border: "1px solid var(--border)",
            }}
          >
            <h4 style={{ margin: "0 0 8px 0", fontSize: "13.5px", fontWeight: 700 }}>
              🎯 How to Qualify &amp; Get Considered
            </h4>
            <ul style={{ margin: 0, paddingLeft: "18px", fontSize: "12.5px", lineHeight: "1.55" }}>
              {tips.tips.map((tip, idx) => (
                <li key={idx} style={{ marginBottom: "4px" }}>{tip}</li>
              ))}
            </ul>
            {tips.avoid.length > 0 && (
              <div
                style={{
                  marginTop: "8px",
                  fontSize: "12px",
                  color: "var(--bad, #b91c1c)",
                  fontWeight: 500,
                }}
              >
                ⚠️ <strong>Common pitfalls to avoid:</strong> {tips.avoid.join(" · ")}
              </div>
            )}
            {tips.focus.length > 0 && (
              <div
                style={{
                  marginTop: "6px",
                  fontSize: "12px",
                  color: "var(--text-dim)",
                }}
              >
                🔑 <strong>Key technical focus:</strong> {tips.focus.join(" · ")}
              </div>
            )}
          </div>

          {/* Section 2: Resume & Skills Alignment */}
          <div
            style={{
              background: "var(--ac-surface-container, rgba(0,0,0,0.03))",
              padding: "12px",
              borderRadius: "8px",
              border: "1px solid var(--border)",
            }}
          >
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px", flexWrap: "wrap", gap: "6px" }}>
              <h4 style={{ margin: 0, fontSize: "13.5px", fontWeight: 700 }}>
                📄 Tailored Resume &amp; Role Alignment
              </h4>
              <button
                type="button"
                className="btn btn-sm btn-outline"
                onClick={handleTailorResume}
                disabled={tailorLoading}
                style={{ fontSize: "11.5px", padding: "3px 10px" }}
              >
                {tailorLoading ? "⏳ Generating..." : tailorResult ? (tailorOpen ? "Hide Resume" : "View Tailored Resume") : "✨ Generate Tailored Resume"}
              </button>
            </div>

            {profile?.has_resume ? (
              <div style={{ fontSize: "12.5px", lineHeight: "1.5" }}>
                {matchedSkills.length > 0 ? (
                  <p style={{ margin: "0 0 6px 0" }}>
                    ✅ <strong>Matching skills on your profile:</strong>{" "}
                    <span style={{ color: "var(--ac-primary, #436436)", fontWeight: 600 }}>
                      {matchedSkills.join(", ")}
                    </span>
                  </p>
                ) : (
                  <p style={{ margin: "0 0 6px 0", color: "var(--text-dim)" }}>
                    No exact keyword matches found in your profile skills. Highlight transferable problem-solving and projects.
                  </p>
                )}
                <p style={{ margin: "0", color: "var(--text-dim)" }}>
                  💡 <strong>CV Action:</strong> Reorder your projects to showcase code relevant to {item.org}. Ensure working GitHub links with automated tests and clear READMEs are at the top of your resume.
                </p>
              </div>
            ) : (
              <p style={{ margin: 0, fontSize: "12.5px", color: "var(--text-dim)" }}>
                Upload your base resume in the Profile tab to receive automatic skill-matching and project recommendations for this opportunity.
              </p>
            )}

            {/* Tailored Resume Container */}
            {tailorOpen && (
              <div
                style={{
                  marginTop: "12px",
                  background: "var(--ac-surface-container, rgba(0,0,0,0.04))",
                  padding: "12px",
                  borderRadius: "8px",
                  border: "1px solid var(--border)",
                }}
              >
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "8px", flexWrap: "wrap", gap: "6px" }}>
                  <span style={{ fontWeight: 700, fontSize: "13px" }}>
                    Tailored Resume &amp; Strategy for {item.org}
                  </span>
                  <div style={{ display: "flex", gap: "6px" }}>
                    {tailorResult?.tailored_resume && (
                      <>
                        <button
                          type="button"
                          className="btn btn-sm btn-ghost"
                          onClick={() => {
                            navigator.clipboard.writeText(tailorResult.tailored_resume || "");
                            setTailorCopied(true);
                            setTimeout(() => setTailorCopied(false), 2000);
                          }}
                          style={{ fontSize: "11px", padding: "2px 8px" }}
                        >
                          {tailorCopied ? "✓ Copied" : "📋 Copy"}
                        </button>
                        <a
                          href={`data:text/markdown;charset=utf-8,${encodeURIComponent(tailorResult.tailored_resume || "")}`}
                          download={`${item.org.toLowerCase().replace(/[^a-z0-9]/g, "_")}_tailored_cv.md`}
                          className="btn btn-sm btn-ghost"
                          style={{ fontSize: "11px", padding: "2px 8px", textDecoration: "none" }}
                          onClick={(e) => e.stopPropagation()}
                        >
                          💾 Download .md
                        </a>
                      </>
                    )}
                    <button
                      type="button"
                      className="btn btn-sm btn-ghost"
                      onClick={() => setTailorOpen(false)}
                      style={{ fontSize: "11px", padding: "2px 8px" }}
                    >
                      ✕ Close
                    </button>
                  </div>
                </div>

                {tailorLoading && (
                  <p style={{ color: "var(--text-dim)", margin: 0, fontSize: "12.5px" }}>
                    ⏳ Grounding and tailoring your resume credentials for {item.title}...
                  </p>
                )}

                {tailorError && (
                  <p style={{ color: "var(--bad, #dc2626)", margin: 0, fontSize: "12.5px" }}>
                    {tailorError}
                  </p>
                )}

                {tailorResult?.tailored_resume && (
                  <div
                    style={{
                      whiteSpace: "pre-wrap",
                      lineHeight: "1.5",
                      maxHeight: "320px",
                      overflowY: "auto",
                      fontSize: "12px",
                      fontFamily: "monospace",
                      color: "var(--text)",
                      borderTop: "1px solid var(--border)",
                      paddingTop: "8px",
                      background: "var(--bg, #fff)",
                      padding: "10px",
                      borderRadius: "6px",
                      boxShadow: "inset 0 1px 3px rgba(0,0,0,0.05)",
                    }}
                  >
                    {tailorResult.tailored_resume}
                  </div>
                )}
              </div>
            )}
          </div>

          {/* SOP generation box */}
          {sopOpen && (
            <div
              style={{
                background: "var(--ac-surface-container, rgba(0,0,0,0.04))",
                padding: "12px",
                borderRadius: "8px",
                border: "1px solid var(--border)",
              }}
            >
              <div
                style={{
                  display: "flex",
                  justifyContent: "space-between",
                  alignItems: "center",
                  marginBottom: "8px",
                }}
              >
                <span style={{ fontWeight: 700, fontSize: "13px" }}>
                  Statement of Purpose / Motivation
                </span>
                <div style={{ display: "flex", gap: "6px" }}>
                  {sopText && (
                    <button
                      type="button"
                      className="btn btn-sm btn-ghost"
                      onClick={() => {
                        navigator.clipboard.writeText(sopText);
                        setSopCopied(true);
                        setTimeout(() => setSopCopied(false), 2000);
                      }}
                      style={{ fontSize: "11px", padding: "2px 8px" }}
                    >
                      {sopCopied ? "✓ Copied" : "📋 Copy"}
                    </button>
                  )}
                  <button
                    type="button"
                    className="btn btn-sm btn-ghost"
                    onClick={() => setSopOpen(false)}
                    style={{ fontSize: "11px", padding: "2px 8px" }}
                  >
                    ✕ Close
                  </button>
                </div>
              </div>
              {sopLoading && (
                <p style={{ color: "var(--text-dim)", margin: 0, fontSize: "12.5px" }}>
                  ⏳ Drafting grounded statement of purpose from your CV &amp; motivation statement...
                </p>
              )}
              {sopError && (
                <p style={{ color: "var(--bad, #dc2626)", margin: 0, fontSize: "12.5px" }}>
                  {sopError}
                </p>
              )}
              {sopText && (
                <div
                  style={{
                    whiteSpace: "pre-wrap",
                    lineHeight: "1.5",
                    maxHeight: "260px",
                    overflowY: "auto",
                    fontSize: "12.5px",
                    color: "var(--text)",
                    borderTop: "1px solid var(--border)",
                    paddingTop: "8px",
                  }}
                >
                  {sopText}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
