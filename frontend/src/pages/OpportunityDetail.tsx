import React, { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, generateSop, tailorOpportunityResumePdf, type Opportunity } from "../api";
import {
  getOpportunityTips,
  fmtDate,
  daysUntil,
} from "../OpportunityCard";

export default function OpportunityDetail() {
  const location = useLocation();
  const navigate = useNavigate();
  const item = location.state?.opportunity as Opportunity | undefined;
  const from = (location.state as { from?: string } | null)?.from;

  // The cards open this page from four tabs, so "back" has to remember which
  // one sent the reader here. The kind is the fallback for a direct link or a
  // reload, where the router state is gone.
  const BACK_LABELS: Record<string, string> = {
    "/opportunities": "Events & Programs",
    "/internships": "Internships",
    "/volunteers": "Volunteer",
    "/speakers": "Speakers",
    "/open-source": "Open source",
    "/mentorship": "Mentorship",
    "/hackathons": "Hackathons",
  };
  const KIND_ROUTES: Record<string, string> = {
    Event: "/opportunities",
    "Paid program": "/opportunities",
    Internship: "/internships",
    Apprenticeship: "/internships",
    Volunteer: "/volunteers",
    "Speaker / CFP": "/speakers",
    "Open source": "/open-source",
    Mentorship: "/mentorship",
    Hackathon: "/hackathons",
  };
  const backTo =
    from && BACK_LABELS[from]
      ? from
      : (item && KIND_ROUTES[item.kind]) || "/opportunities";
  const backLabel = BACK_LABELS[backTo] ?? "Events & Programs";

  const [sopLoading, setSopLoading] = useState(false);
  const [sopText, setSopText] = useState("");
  const [sopError, setSopError] = useState("");
  const [sopCopied, setSopCopied] = useState(false);

  const [pdfLoading, setPdfLoading] = useState(false);
  const [pdfError, setPdfError] = useState("");

  const { data: profile } = useQuery({
    queryKey: ["profile"],
    queryFn: api.profile,
    staleTime: 5 * 60_000,
  });

  if (!item) {
    // No opportunity passed via state — redirect back
    navigate("/internships", { replace: true });
    return null;
  }

  const tips = getOpportunityTips(item);

  const matchedSkills = (profile?.skills ?? []).filter((skill) => {
    const hay = `${item.title} ${item.desc} ${item.tags.join(" ")}`.toLowerCase();
    return hay.includes(skill.toLowerCase());
  });

  const handleGenerateSop = async (e: React.MouseEvent) => {
    e.preventDefault();
    if (sopLoading) return;
    setSopLoading(true);
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
      setSopError(err instanceof Error ? err.message : "Failed to generate SOP.");
    } finally {
      setSopLoading(false);
    }
  };

  const handleDownloadPdf = async (e: React.MouseEvent) => {
    e.preventDefault();
    if (pdfLoading) return;
    setPdfLoading(true);
    setPdfError("");
    try {
      const blob = await tailorOpportunityResumePdf({
        title: item.title,
        org: item.org,
        kind: item.kind,
        description: item.desc || `${item.title} by ${item.org}`,
        tags: item.tags,
      });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${item.org.toLowerCase().replace(/[^a-z0-9]/g, "_")}_tailored_resume.pdf`;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    } catch (err: unknown) {
      setPdfError(err instanceof Error ? err.message : "Failed to download tailored resume.");
    } finally {
      setPdfLoading(false);
    }
  };

  const deadline =
    item.closes ??
    (item.kind === "Event" || item.kind === "Hackathon" ? item.date : null) ??
    null;

  return (
    <>
      {/* Back link */}
      <Link
        to={backTo}
        className="btn-ghost btn-sm"
        aria-label={`Back to ${backLabel}`}
        style={{ marginBottom: 14, display: "inline-flex", alignItems: "center", gap: 4 }}
      >
        <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 18 }}>arrow_back</span>
        Back to {backLabel}
      </Link>

      {/* Header */}
      <div className="detail-head">
        <div className="detail-head-main">
          <div className="detail-head-info">
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 6, flexWrap: "wrap" }}>
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
                <span className="chip" style={{ color: "var(--bad)" }}>Closed</span>
              )}
              <span className="chip">via {item.source}</span>
            </div>
            <h2 className="detail-title">{item.title}</h2>
            <div className="detail-meta">
              <strong style={{ color: "var(--text)" }}>{item.org}</strong>
              <span>·</span>
              <span>{item.city}</span>
              {item.when && <><span>·</span><span>{item.when}</span></>}
            </div>
          </div>
        </div>

        <div className="detail-actions">
          {deadline && (
            <a
              href={`/api/opportunities/calendar?url=${encodeURIComponent(item.url)}&title=${encodeURIComponent(item.title)}&org=${encodeURIComponent(item.org)}&deadline=${encodeURIComponent(deadline)}`}
              download
              className="btn btn-sm btn-ghost"
              title="Download .ics to add to your calendar"
            >
              <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 16 }}>calendar_month</span>
              Add to Calendar
            </a>
          )}
          <a
            href={item.url}
            target="_blank"
            rel="noopener noreferrer"
            className="btn-primary btn-sm"
          >
            Open Official Advert ↗
          </a>
        </div>
      </div>

      {/* Funding badges */}
      {(item.stipend_amount || item.accommodation === "included" || item.airfare_covered || item.relocation_support || item.remote_friendly) && (
        <div className="card">
          <h3 className="card-title">
            <span className="material-symbols-outlined" aria-hidden="true">payments</span>
            Funding &amp; Benefits
          </h3>
          <Funding item={item} />
          {item.stipend && <p className="opp-stipend" style={{ marginTop: 8 }}>💰 {item.stipend}</p>}
        </div>
      )}

      {/* Full details card */}
      <div className="card">
        <h3 className="card-title">
          <span className="material-symbols-outlined" aria-hidden="true">badge</span>
          Opportunity Details
        </h3>
        <div className="grid2">
          <div>
            <label>Organisation</label>
            <p className="cell-dim" style={{ margin: "2px 0 0" }}>{item.org}</p>
          </div>
          <div>
            <label>Location</label>
            <p className="cell-dim" style={{ margin: "2px 0 0" }}>{item.city || "Not stated"}</p>
          </div>
          {item.opens && (
            <div>
              <label>Opens</label>
              <p className="cell-dim" style={{ margin: "2px 0 0" }}>{fmtDate(item.opens)}</p>
            </div>
          )}
          {item.closes && (
            <div>
              <label>Closes</label>
              <p className="cell-dim" style={{ margin: "2px 0 0" }}>
                {fmtDate(item.closes)}
                {!item.expired && daysUntil(item.closes) >= 0 && (
                  <span style={{ marginLeft: 6, color: daysUntil(item.closes) <= 7 ? "var(--bad)" : "var(--text-dim)" }}>
                    ({daysUntil(item.closes) === 0 ? "today!" : `${daysUntil(item.closes)} days left`})
                  </span>
                )}
              </p>
            </div>
          )}
        </div>

        <div className="opp-timeline" style={{ marginTop: 12 }}>
          <Timeline item={item} />
        </div>

        {item.desc && (
          <div className="field" style={{ marginTop: 12 }}>
            <label>Description</label>
            <p className="prose" style={{ marginTop: 4 }}>{item.desc}</p>
          </div>
        )}

        <Eligibility item={item} />

        {item.tags.length > 0 && (
          <div className="opp-tags" style={{ marginTop: 10 }}>
            {item.tags.map((tag) => (
              <span key={tag} className="opp-tag">{tag}</span>
            ))}
          </div>
        )}

        {item.steps && item.steps.length > 0 && (
          <div className="field" style={{ marginTop: 12 }}>
            <label>Application Steps</label>
            <ol className="opp-steps" style={{ marginTop: 4 }}>
              {item.steps.map((step) => (
                <li key={step}>{step}</li>
              ))}
            </ol>
          </div>
        )}
      </div>

      {/* How to Qualify */}
      <div className="card">
        <h3 className="card-title">
          <span className="material-symbols-outlined" aria-hidden="true">target</span>
          How to Qualify &amp; Get Considered
        </h3>

        <ul className="prose" style={{ paddingLeft: 18, margin: "0 0 10px 0" }}>
          {tips.tips.map((tip, idx) => (
            <li key={idx} style={{ marginBottom: 4 }}>{tip}</li>
          ))}
        </ul>

        {tips.avoid.length > 0 && (
          <div style={{ fontSize: "13px", color: "var(--bad, #b91c1c)", fontWeight: 500, marginBottom: 8 }}>
            ⚠️ <strong>Common pitfalls to avoid:</strong> {tips.avoid.join(" · ")}
          </div>
        )}

        {tips.focus.length > 0 && (
          <div style={{ fontSize: "13px", color: "var(--text-dim)" }}>
            🔑 <strong>Key technical focus:</strong> {tips.focus.join(" · ")}
          </div>
        )}
      </div>

      {/* Resume & Skills */}
      <div className="card">
        <h3 className="card-title">
          <span className="material-symbols-outlined" aria-hidden="true">description</span>
          Resume &amp; Skills Alignment
        </h3>

        {profile?.has_resume ? (
          <>
            {matchedSkills.length > 0 ? (
              <p style={{ margin: "0 0 8px 0" }}>
                ✅ <strong>Matching skills on your profile:</strong>{" "}
                <span style={{ color: "var(--ac-primary, #436436)", fontWeight: 600 }}>
                  {matchedSkills.join(", ")}
                </span>
              </p>
            ) : (
              <p className="cell-dim" style={{ margin: "0 0 8px 0" }}>
                No exact keyword matches found. Highlight transferable problem-solving and projects.
              </p>
            )}
            <p className="cell-dim" style={{ margin: 0 }}>
              💡 <strong>CV Action:</strong> Reorder your projects to showcase code relevant to {item.org}. Ensure working GitHub links with automated tests and clear READMEs are at the top.
            </p>
          </>
        ) : (
          <p className="cell-dim" style={{ margin: 0 }}>
            Upload your base resume in the Profile tab to receive automatic skill-matching and project recommendations for this opportunity.
          </p>
        )}

        <div className="artifact-actions" style={{ marginTop: 14 }}>
          <button
            type="button"
            className="btn btn-sm"
            onClick={handleDownloadPdf}
            disabled={pdfLoading}
            title="Tailor your resume for this opportunity and download as PDF"
          >
            <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 16 }}>download</span>
            {pdfLoading ? "Generating PDF…" : "Download Tailored Resume (PDF)"}
          </button>
        </div>
        {pdfError && (
          <div className="banner banner-bad" role="alert" style={{ marginTop: 8 }}>{pdfError}</div>
        )}
      </div>

      {/* Statement of Purpose */}
      <div className="card">
        <h3 className="card-title">
          <span className="material-symbols-outlined" aria-hidden="true">edit_note</span>
          Statement of Purpose
        </h3>

        {!sopText ? (
          <>
            <p className="cell-dim" style={{ marginTop: 0 }}>
              Generate a grounded statement of purpose tailored to this opportunity, based on your resume and motivation statement.
            </p>
            <button
              type="button"
              className="btn btn-sm"
              onClick={handleGenerateSop}
              disabled={sopLoading}
            >
              <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 16 }}>auto_fix_high</span>
              {sopLoading ? "Generating SOP…" : "Generate Statement of Purpose"}
            </button>
          </>
        ) : (
          <>
            <div
              style={{
                whiteSpace: "pre-wrap",
                lineHeight: 1.6,
                fontSize: "14px",
                color: "var(--text)",
                marginBottom: 10,
              }}
            >
              {sopText}
            </div>
            <div className="artifact-actions">
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => {
                  void navigator.clipboard.writeText(sopText);
                  setSopCopied(true);
                  setTimeout(() => setSopCopied(false), 2000);
                }}
              >
                {sopCopied ? "✓ Copied" : "Copy SOP"}
              </button>
              <button
                type="button"
                className="btn-ghost btn-sm"
                onClick={() => { setSopText(""); setSopError(""); }}
              >
                Regenerate
              </button>
            </div>
          </>
        )}
        {sopError && (
          <div className="banner banner-bad" role="alert" style={{ marginTop: 8 }}>{sopError}</div>
        )}
      </div>

      {/* Primary CTA */}
      <div style={{ display: "flex", justifyContent: "center", padding: "16px 0 24px" }}>
        <a
          href={item.url}
          target="_blank"
          rel="noopener noreferrer"
          className="btn btn-primary"
          style={{ padding: "10px 28px", fontSize: "15px", fontWeight: 600 }}
        >
          <span className="material-symbols-outlined" aria-hidden="true" style={{ fontSize: 18 }}>open_in_new</span>
          Open Official Advert / Apply ↗
        </a>
      </div>
    </>
  );
}

// Re-export sub-components used by OpportunityDetail so they can be imported
// from OpportunityCard (they are defined there already, we just alias them here
// for internal use; the real exports come from OpportunityCard.tsx).
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

function Eligibility({ item }: { item: Opportunity }) {
  const labels = (item.eligibility ?? [])
    .map((key) => ELIGIBILITY_LABELS[key])
    .filter(Boolean);
  if (labels.length === 0) return null;
  return (
    <p className="opp-eligibility" style={{ marginTop: 10 }}>
      Eligibility: {labels.join(" · ")}
    </p>
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
      </>
    );
  }

  if (item.closes) {
    const left = item.expired ? -1 : daysUntil(item.closes);
    const opens = item.opens ?? "";
    const opening = opens ? daysUntil(opens) : 0;
    if (left < 0) {
      return (
        <p>
          🔒 <b>Applications closed</b> · Deadline <b>{fmtDate(item.closes)}</b>
        </p>
      );
    }
    return (
      <p>
        {opening > 0 ? (
          <>⏳ Opens <b>{fmtDate(opens)}</b> (in {opening} day{opening === 1 ? "" : "s"})</>
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
    );
  }

  if (item.date) {
    const left = daysUntil(item.date);
    return (
      <p>
        📅 {left >= 0 ? (
          <>Happens <b>{fmtDate(item.date)}</b> · in {left} day{left === 1 ? "" : "s"}</>
        ) : (
          <>Was on <b>{fmtDate(item.date)}</b></>
        )}
      </p>
    );
  }

  return <p>🔁 Rolling / cohort-based — apply any time or when announced</p>;
}
