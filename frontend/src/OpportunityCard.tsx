import type { Opportunity } from "./api";
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

export const daysUntil = (iso: string) =>
  Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000);

export const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });

/** Status shown in the box at the bottom of each card. */
export function opportunityStatus(o: Opportunity): string {
  if (o.kind === "Event") return o.date && daysUntil(o.date) < 0 ? "Closed" : "Upcoming event";
  // Prefer the server's verdict when it sent one; the local clock is only a
  // fallback for cached payloads from before the field existed.
  if (o.status) {
    return o.status === "closed" ? "Closed"
      : o.status === "opening_soon" ? "Opening soon"
      : o.status === "open" ? "Open now"
      : "Rolling";
  }
  // A published deadline is enough to be open: a volunteer call advertises
  // when it closes, not when it began, so a missing `opens` means "already
  // open" rather than "no application window".
  if (o.closes) {
    if (o.expired ?? daysUntil(o.closes) < 0) return "Closed";
    return !o.opens || daysUntil(o.opens) <= 0 ? "Open now" : "Opening soon";
  }
  // Crew calls lifted from the events calendar carry the event's date.
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
            {daysUntil(item.date) >= 0 ? `in ${daysUntil(item.date)} days` : "passed"}
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
              ⏳ Opens <b>{fmtDate(opens)}</b> (in {opening} days)
            </>
          ) : left <= 7 && left >= 0 ? (
            <>🚨 <b>Closing soon</b></>
          ) : (
            <>🟢 Open now</>
          )}{" "}
          · Deadline <b>{fmtDate(item.closes)}</b>
          {left >= 0 && <> ({left === 0 ? "closes today!" : `${left} days left`})</>}
        </p>
        <Steps item={item} />
      </>
    );
  }

  // A crew call found in the events calendar is tied to the event it belongs
  // to, so the event's own date is what the card counts down to.
  if (item.date) {
    const left = daysUntil(item.date);
    return (
      <>
        <p>
          📅 {left >= 0 ? (
            <>
              Happens <b>{fmtDate(item.date)}</b> · in {left} days
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
    currentlyWatched: boolean,
  ) => void;
}

/** Shared card for the events/programs list and the internships page. */
export function OpportunityCard({
  item,
  isWatched = false,
  onToggleWatch,
}: OpportunityCardProps) {
  const deadline = item.closes ?? (item.kind === "Event" ? item.date : null) ?? null;
  // Tracking is per application, so volunteer and speaking slots count too.
  const tracking = useOpportunityTracking();
  const stage = tracking.stageFor(item.url);

  return (
    <a
      className="opp-card"
      href={item.url}
      target="_blank"
      rel="noreferrer"
    >
      <div className="opp-card-top">
        <span
          className=        {
            "opp-kind" +
            (item.kind === "Paid program" ? " opp-kind-program"
              : item.kind === "Apprenticeship" ? " opp-kind-apprentice"
              : item.kind === "Internship" ? " opp-kind-intern"
              : item.kind === "Volunteer" ? " opp-kind-volunteer" : "")
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
            title={isWatched ? "Remove deadline alert" : "Get notified 5 days before deadline"}
            aria-label={isWatched ? "Remove deadline alert" : "Notify me of deadline"}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onToggleWatch(item.url, item.title, item.org, item.kind, deadline, isWatched);
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
    </a>
  );
}
