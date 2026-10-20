import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";
import PublicNav from "../PublicNav";
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
const TYPES = ["All", "Internship", "Paid program"] as const;
const BASE_LOCATIONS = ["Tokyo", "Kenya", "Online"];

export default function Internships({ embedded = false }: { embedded?: boolean }) {
  const internships = useQuery({
    queryKey: ["internships"],
    queryFn: api.internships,
    staleTime: 5 * 60_000,
  });
  // Signed-in users get paid programs here too; visitors only see placements.
  const opportunities = useQuery({
    queryKey: ["opportunities"],
    queryFn: api.opportunities,
    staleTime: 5 * 60_000,
    enabled: embedded,
  });

  const data = internships.data;
  const isPending = internships.isPending || (embedded && opportunities.isPending);
  const error = internships.error ?? (embedded ? opportunities.error : null);

  const items = useMemo(() => {
    const placements = internships.data?.items ?? [];
    const programs = embedded
      ? (opportunities.data?.items ?? []).filter((o) => o.kind === "Paid program")
      : [];
    return [...placements, ...programs];
  }, [internships.data, opportunities.data, embedded]);

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

  const paid = items.filter((o) => o.kind === "Internship" && o.stipend?.startsWith("Paid:")).length;
  const unpaid = items.filter((o) => o.kind === "Internship" && o.stipend?.startsWith("Unpaid")).length;
  const programs = items.filter((o) => o.kind === "Paid program").length;

  const blurb = embedded ? (
    <>
      Internships and paid programs in one place, with compensation, location
      and application steps stated upfront. Deadlines are estimated from past
      cycles — confirm on the official site.
    </>
  ) : (
    <>
      Paid, unpaid-with-benefits and free internships, with compensation,
      location and application steps stated upfront. Deadlines are estimated
      from past cycles — confirm on the official site.
    </>
  );

  return (
    <div className={`opp-page${embedded ? " opp-embedded" : ""}`}>
      {embedded ? (
        <div className="page-intro">
          <h2 className="page-title">Internships &amp; Opportunities</h2>
          <p className="page-sub">{blurb}</p>
        </div>
      ) : (
        <>
          <PublicNav />
          <a className="skip-link" href="#main">Skip to content</a>
          <header className="opp-header">
            <div className="opp-header-inner">
              <h1>Internships</h1>
              <p>{blurb}</p>
            </div>
          </header>
        </>
      )}

      <main id={embedded ? undefined : "main"} className="opp-layout">
        <aside className="opp-filters" aria-label="Filters">
          <input
            type="search"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder={embedded ? "Search internships and programs…" : "Search internships…"}
            aria-label={embedded ? "Search internships and programs" : "Search internships"}
          />

          {embedded && (
            <div className="opp-filter-group">
              <p className="opp-filter-label" id="intern-type">Type</p>
              <div className="opp-chips" role="group" aria-labelledby="intern-type">
                {TYPES.map((t) => (
                  <button
                    key={t}
                    type="button"
                    className="opp-chip"
                    aria-pressed={type === t}
                    onClick={() => setType(t)}
                  >
                    {t}
                  </button>
                ))}
              </div>
            </div>
          )}

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
          {isPending && <Loading label={embedded ? "Loading internships and programs" : "Loading internships"} />}
          {error && <ErrorBox error={error} />}

          {data && !isPending && (
            <>
              <p className="opp-count">
                {results.length} results · {paid} paid · {unpaid} unpaid with benefits
                {programs > 0 && ` · ${programs} paid programs`}
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
