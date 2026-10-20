import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api, type Opportunity } from "../api";
import { ErrorBox, Loading, relTime } from "../components";

/**
 * Public "Events & paid programs" screen — the standalone page from the design
 * pack (canary-career-spark.lovable.app/opportunities), served from live data.
 *
 * Events are fetched by the backend from Tech Group Kenya's public calendar
 * (app/opportunities.py) and paid programs are curated there, so this page
 * only renders whatever `GET /api/public/opportunities` returns. Every filter
 * below is derived from that payload — a new listing, city or category needs
 * no frontend change.
 */

const TYPES = ["All", "Event", "Internship", "Paid program"] as const;
const STATUSES = ["All", "Open now", "Opening soon", "Rolling", "Upcoming event"] as const;
/** Cities the design ships with; kept even when the feed has no event there. */
const BASE_LOCATIONS = ["Nairobi", "Kisumu", "Eldoret", "Kenya", "Online"];

const daysUntil = (iso: string) =>
  Math.ceil((new Date(iso).getTime() - Date.now()) / 86_400_000);

const fmtDate = (iso: string) =>
  new Date(iso).toLocaleDateString("en-GB", { day: "numeric", month: "short", year: "numeric" });

/** Filter chip — single-select for type/status/location, toggle for interests. */
function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: string }) {
  return (
    <button type="button" className="opp-chip" aria-pressed={on} onClick={onClick}>
      {children}
    </button>
  );
}

/** Status shown in the box at the bottom of each card. */
function status(o: Opportunity): string {
  if (o.kind === "Event") return o.date && daysUntil(o.date) < 0 ? "Closed" : "Upcoming event";
  if (!o.opens || !o.closes) return "Rolling";
  if (daysUntil(o.closes) < 0) return "Closed";
  return daysUntil(o.opens) <= 0 ? "Open now" : "Opening soon";
}

/** Programs with a deadline sort first, then events by date; rolling entries last. */
const deadline = (o: Opportunity) =>
  o.closes ? new Date(o.closes).getTime()
  : o.date ? new Date(o.date).getTime()
  : 9e15;

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

function Steps({ item }: { item: Opportunity }) {
  if (!item.steps) return null;
  return (
    <ol className="opp-steps">
      {item.steps.map((step) => <li key={step}>{step}</li>)}
    </ol>
  );
}

export default function Opportunities() {
  const { data, isPending, error } = useQuery({
    queryKey: ["opportunities"],
    queryFn: api.opportunities,
    // The backend caches the feed for 15 minutes; don't refetch on every mount.
    staleTime: 5 * 60_000,
  });

  const items = data?.items ?? [];
  const [query, setQuery] = useState("");
  const [type, setType] = useState("All");
  const [statusFilter, setStatusFilter] = useState("All");
  const [city, setCity] = useState("All");
  const [tags, setTags] = useState<string[]>([]);

  const locations = useMemo(() => {
    const extra = items.map((o) => o.city).filter((c) => c && !BASE_LOCATIONS.includes(c));
    return ["All", ...BASE_LOCATIONS, ...new Set(extra)];
  }, [items]);

  const interests = useMemo(
    () => [...new Set(items.flatMap((o) => o.tags))].sort(),
    [items],
  );

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter(
      (o) =>
        (type === "All" || o.kind === type) &&
        (city === "All" || o.city === city) &&
        (statusFilter === "All" || status(o) === statusFilter) &&
        (tags.length === 0 || tags.some((t) => o.tags.includes(t))) &&
        (!q || `${o.title} ${o.org} ${o.desc} ${o.tags.join(" ")}`.toLowerCase().includes(q)),
    ).sort((a, b) => deadline(a) - deadline(b));
  }, [items, query, type, city, tags, statusFilter]);

  const toggleTag = (tag: string) =>
    setTags((old) => (old.includes(tag) ? old.filter((t) => t !== tag) : [...old, tag]));

  const filtersOn = tags.length > 0 || city !== "All" || statusFilter !== "All" || type !== "All" || query !== "";
  const clearAll = () => {
    setTags([]);
    setCity("All");
    setType("All");
    setQuery("");
    setStatusFilter("All");
  };

  return (
    <div className="opp-page">
      <a className="skip-link" href="#main">Skip to content</a>

      <header className="opp-header">
        <div className="opp-header-inner">
          <Link to="/" className="opp-back">← ApplyCanary</Link>
          <h1>Events, internships &amp; paid programs</h1>
          <p>
            Tech events near you in Kenya plus internships and programs — paid,
            unpaid-with-benefits or free — with application timelines sorted by
            nearest deadline. Dates are estimated from past cycles; confirm on
            the official site.
          </p>
        </div>
      </header>

      <main id="main" className="opp-layout">
        <aside className="opp-filters" aria-label="Filters">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search events, programs…"
            aria-label="Search events and programs"
          />

          <div className="opp-filter-group">
            <p className="opp-filter-label" id="opp-type">Type</p>
            <div className="opp-chips" role="group" aria-labelledby="opp-type">
              {TYPES.map((t) => (
                <Chip key={t} on={type === t} onClick={() => setType(t)}>{t}</Chip>
              ))}
            </div>
          </div>

          <div className="opp-filter-group">
            <p className="opp-filter-label" id="opp-status">Application status</p>
            <div className="opp-chips" role="group" aria-labelledby="opp-status">
              {STATUSES.map((s) => (
                <Chip key={s} on={statusFilter === s} onClick={() => setStatusFilter(s)}>{s}</Chip>
              ))}
            </div>
          </div>

          <div className="opp-filter-group">
            <p className="opp-filter-label" id="opp-location">Location</p>
            <div className="opp-chips" role="group" aria-labelledby="opp-location">
              {locations.map((l) => (
                <Chip key={l} on={city === l} onClick={() => setCity(l)}>{l}</Chip>
              ))}
            </div>
          </div>

          <div className="opp-filter-group">
            <p className="opp-filter-label" id="opp-interests">Interests</p>
            <div className="opp-chips" role="group" aria-labelledby="opp-interests">
              {interests.map((t) => (
                <Chip key={t} on={tags.includes(t)} onClick={() => toggleTag(t)}>{t}</Chip>
              ))}
            </div>
          </div>

          {filtersOn && (
            <button type="button" className="opp-clear" onClick={clearAll}>Clear filters</button>
          )}
        </aside>

        <section className="opp-results" aria-live="polite">
          {isPending && <Loading label="Loading events &amp; programs" />}
          {error && <ErrorBox error={error} />}

          {data && !data.events_live && (
            <div className="banner banner-warn" role="status">
              {data.events_updated_at
                ? `The live event feed could not refresh — showing events last updated ${relTime(data.events_updated_at)}.`
                : "The live event feed is unavailable right now — paid programs below are unaffected."}
            </div>
          )}

          {data && (
            <>
              <p className="opp-count">{results.length} results</p>

              {results.length === 0 && (
                <div className="opp-empty">Nothing matches yet. Try removing a filter.</div>
              )}

              <div className="opp-grid">
                {results.map((item) => (
                  <a
                    key={`${item.kind}:${item.title}`}
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
                ))}
              </div>
            </>
          )}
        </section>
      </main>
    </div>
  );
}
