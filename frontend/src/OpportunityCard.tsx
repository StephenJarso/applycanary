import type { Opportunity } from "./api";

export const daysUntil = (iso: string) =>
  Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000);

export const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });

/** Status shown in the box at the bottom of each card. */
export function opportunityStatus(o: Opportunity): string {
  if (o.kind === "Event") return o.date && daysUntil(o.date) < 0 ? "Closed" : "Upcoming event";
  if (!o.opens || !o.closes) return "Rolling";
  if (daysUntil(o.closes) < 0) return "Closed";
  return daysUntil(o.opens) <= 0 ? "Open now" : "Opening soon";
}

/** Dated entries sort first, then rolling ones last. */
export const opportunityDeadline = (o: Opportunity) =>
  o.closes ? new Date(o.closes).getTime()
  : o.date ? new Date(o.date).getTime()
  : 9e15;

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

  if (item.opens && item.closes) {
    return (
      <>
        <p>
          {daysUntil(item.opens) > 0 ? (
            <>
              ⏳ Opens <b>{fmtDate(item.opens)}</b> (in {daysUntil(item.opens)} days)
            </>
          ) : (
            <>🟢 Open now</>
          )}{" "}
          · Deadline <b>{fmtDate(item.closes)}</b>
          {daysUntil(item.closes) >= 0 && <> ({daysUntil(item.closes)} days left)</>}
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

/** Shared card for the events/programs list and the internships page. */
export function OpportunityCard({ item }: { item: Opportunity }) {
  return (
    <a
      className="opp-card"
      href={item.url}
      target="_blank"
      rel="noreferrer"
    >
      <div className="opp-card-top">
        <span
          className={
            "opp-kind" +
            (item.kind === "Paid program" ? " opp-kind-program"
              : item.kind === "Internship" ? " opp-kind-intern" : "")
          }
        >
          {item.kind}
        </span>
        <span className="opp-source">via {item.source}</span>
      </div>

      <div>
        <h3>{item.title}</h3>
        <p className="opp-meta">{item.org} · {item.city} · {item.when}</p>
      </div>

      {item.desc && <p className="opp-desc">{item.desc}</p>}
      {item.stipend && <p className="opp-stipend">💰 {item.stipend}</p>}

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
