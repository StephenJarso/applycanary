import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { ErrorBox, Loading, relTime } from "../components";
import {
  OpportunityCard,
  opportunityDeadline as deadline,
  opportunityStatus as status,
} from "../OpportunityCard";

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

const TYPES = ["All", "Event", "Paid program"] as const;
const STATUSES = ["All", "Open now", "Opening soon", "Rolling", "Upcoming event"] as const;
/** Cities the design ships with; kept even when the feed has no event there. */
const BASE_LOCATIONS = ["Nairobi", "Kisumu", "Eldoret", "Kenya", "Online"];

/** Filter chip — single-select for type/status/location, toggle for interests. */
function Chip({ on, onClick, children }: { on: boolean; onClick: () => void; children: string }) {
  return (
    <button type="button" className="opp-chip" aria-pressed={on} onClick={onClick}>
      {children}
    </button>
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
          <h1>Events &amp; paid programs</h1>
          <p>
            Tech events near you in Kenya and programs that pay you to learn, with
            application timelines sorted by nearest deadline. Program dates are
            estimated from past cycles — confirm on the official site.
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
                  <OpportunityCard key={`${item.kind}:${item.title}`} item={item} />
                ))}
              </div>
            </>
          )}
        </section>
      </main>
    </div>
  );
}
