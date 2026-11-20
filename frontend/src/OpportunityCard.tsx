import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, generateSop, type Opportunity } from "./api";
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
function getOpportunityTips(item: Opportunity): { tips: string[]; avoid: string[]; focus: string[] } {
  const t = `${item.title} ${item.org} ${item.desc}`.toLowerCase();

  if (t.includes("hennge")) {
    return {
      tips: [
        "Pass 100% of the online coding challenge (Python, Go, or TypeScript) — test all boundary cases and invalid inputs before submitting.",
        "Ensure your GitHub profile showcases at least one functional backend or full-stack project in Go or Python.",
        "In your application essays, explain specifically why HENNGE Tokyo (cloud security, B2B SaaS) and your interest in working in Japan.",
        "Prepare for the technical interview by reviewing REST APIs, async concurrency, and Linux server basics.",
      ],
      avoid: [
        "Submitting incomplete solutions on the coding assessment.",
        "Using generic AI-sounding responses for the motivation questions.",
      ],
      focus: ["Go / Python backend development", "API & cloud fundamentals", "Clean code & edge testing"],
    };
  }

  if (t.includes("outreachy")) {
    return {
      tips: [
        "Pick ONE participating open-source project immediately on Day 1 of the contribution period.",
        "Introduce yourself on the project's chat (Zulip, Matrix, Slack, or mailing list) and follow their contributor setup guide.",
        "Start with beginner-friendly documentation or unit test PRs to get mentor recognition early.",
        "Crucial: Log EVERY single contribution (PR, issue comment, documentation) on the Outreachy website before the period closes.",
        "Ensure you meet the 40 hr/week availability requirement during the internship timeline.",
      ],
      avoid: [
        "Waiting until the last week of the contribution period to submit your first PR.",
        "Jumping between 4 different organizations without substantive merged PRs in any.",
        "Forgetting to record your contributions on the Outreachy portal.",
      ],
      focus: ["Git / GitHub collaboration", "Issue reproduction & testing", "Consistent communication with mentors"],
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

function Funding({ item }: { item: Opportunity }) {
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

function Eligibility({ item }: { item: Opportunity }) {
  const labels = (item.eligibility ?? [])
    .map((key) => ELIGIBILITY_LABELS[key])
    .filter(Boolean);
  if (labels.length === 0) return null;
  return <p className="opp-eligibility">Eligibility: {labels.join(" · ")}</p>;
}

function Steps({ item }: { item: Opportunity }) {
  if (!item.steps) return null;
  return (
    <ol className="opp-steps">
      {item.steps.map((step) => <li key={step}>{step}</li>)}
    </ol>
  );
}

function Timeline({ item }: { item: Opportunity }) {
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
  const deadline =
    item.closes ??
    (item.kind === "Event" || item.kind === "Hackathon" ? item.date : null) ??
    null;

  const tracking = useOpportunityTracking();
  const stage = tracking.stageFor(item.url);

  const [expanded, setExpanded] = useState(false);
  const [sopOpen, setSopOpen] = useState(false);
  const [sopLoading, setSopLoading] = useState(false);
  const [sopText, setSopText] = useState("");
  const [sopCopied, setSopCopied] = useState(false);
  const [sopError, setSopError] = useState("");

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

  return (
    <div
      className={`opp-card${expanded ? " opp-card-expanded" : ""}`}
      onClick={() => setExpanded((prev) => !prev)}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          setExpanded((prev) => !prev);
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
            ? "▲ Tap to collapse details"
            : "▼ Tap for tips, what to do to qualify & tailored resume"}
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
            <h4 style={{ margin: "0 0 6px 0", fontSize: "13.5px", fontWeight: 700 }}>
              📄 Tailored Resume Recommendations
            </h4>
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
                  💡 <strong>CV Action:</strong> Reorder your projects to showcase code relevant to {item.org}. Ensure working GitHub links are at the top of your resume.
                </p>
              </div>
            ) : (
              <p style={{ margin: 0, fontSize: "12.5px", color: "var(--text-dim)" }}>
                Upload your base resume in the Profile tab to receive automatic skill-matching and project recommendations for this opportunity.
              </p>
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
