import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import { ErrorBox, Loading } from "../components";
import {
  OpportunityCard,
  opportunityDeadline as deadline,
  opportunityStatus as status,
} from "../OpportunityCard";

/**
 * Public Internships screen. Same card and filter vocabulary as the events
 * page, but the feed is curated internship placements only, so pay, benefits
 * and location are visible without wading through events or open-source
 * programs.
 */

const STATUSES = ["All", "Open now", "Opening soon", "Rolling"] as const;
const BASE_LOCATIONS = ["Tokyo", "Kenya", "Online"];

export default function Internships() {
  const { data, isPending, error } = useQuery({
    queryKey: ["internships"],
    queryFn: api.internships,
    staleTime: 5 * 60_000,
  });

  const items = data?.items ?? [];
  const [query, setQuery] = useState("");
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
        (city === "All" || o.city === city) &&
        (statusFilter === "All" || status(o) === statusFilter) &&
        (tags.length === 0 || tags.some((t) => o.tags.includes(t))) &&
        (!q || `${o.title} ${o.org} ${o.desc} ${o.tags.join(" ")}`.toLowerCase().includes(q)),
    ).sort((a, b) => deadline(a) - deadline(b));
  }, [items, query, city, tags, statusFilter]);

  const toggleTag = (tag: string) =>
    setTags((old) => (old.includes(tag) ? old.filter((t) => t !== tag) : [...old, tag]));

  const filtersOn = tags.length > 0 || city !== "All" || statusFilter !== "All" || query !== "";
  const clearAll = () => {
    setTags([]);
    setCity("All");
    setQuery("");
    setStatusFilter("All");
  };

  const paid = items.filter((o) => o.stipend?.startsWith("Paid:")).length;
  const unpaid = items.filter((o) => o.stipend?.startsWith("Unpaid")).length;

  return (
    <div className="opp-page">
      <a className="skip-link" href="#main">Skip to content</a>

      <header className="opp-header">
        <div className="opp-header-inner">
          <Link to="/" className="opp-back">← ApplyCanary</Link>
          <h1>Internships</h1>
          <p>
            Paid, unpaid-with-benefits and free internships, with compensation,
            location and application steps stated upfront. Deadlines are
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
            placeholder="Search internships…"
            aria-label="Search internships"
          />

          <div className="opp-filter-group">
            <p className="opp-filter-label" id="intern-status">Application status</p>
            <div className="opp-chips" role="group" aria-labelledby="intern-status">
              {STATUSES.map((s) => (
                <button
                  key={s}
                  type="button"
                  className="opp-chip"
                  aria-pressed={statusFilter === s}
                  onClick={() => setStatusFilter(s)}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          <div className="opp-filter-group">
            <p className="opp-filter-label" id="intern-location">Location</p>
            <div className="opp-chips" role="group" aria-labelledby="intern-location">
              {locations.map((l) => (
                <button
                  key={l}
                  type="button"
                  className="opp-chip"
                  aria-pressed={city === l}
                  onClick={() => setCity(l)}
                >
                  {l}
                </button>
              ))}
            </div>
          </div>

          <div className="opp-filter-group">
            <p className="opp-filter-label" id="intern-interests">Pay &amp; interests</p>
            <div className="opp-chips" role="group" aria-labelledby="intern-interests">
              {interests.map((t) => (
                <button
                  key={t}
                  type="button"
                  className="opp-chip"
                  aria-pressed={tags.includes(t)}
                  onClick={() => toggleTag(t)}
                >
                  {t}
                </button>
              ))}
            </div>
          </div>

          {filtersOn && (
            <button type="button" className="opp-clear" onClick={clearAll}>Clear filters</button>
          )}
        </aside>

        <section className="opp-results" aria-live="polite">
          {isPending && <Loading label="Loading internships" />}
          {error && <ErrorBox error={error} />}

          {data && (
            <>
              <p className="opp-count">
                {results.length} results · {paid} paid · {unpaid} unpaid with benefits
              </p>

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
