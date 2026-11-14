import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getWatched, unwatchOpp, watchOpp } from "../api";
import { useAuth } from "../context/AuthContext";
import PublicNav from "../PublicNav";
import { ErrorBox, Loading } from "../components";
import {
  OpportunityCard,
  opportunityDeadline as deadline,
  opportunityStatus as status,
} from "../OpportunityCard";
import {
  getVapidPublicKey,
  isPushSupported,
  subscribeToPush,
} from "../pushSubscription";

/**
 * Hackathons screen. Same card and filter vocabulary as the internships and
 * volunteer pages, but every entry has to answer two questions up front: what
 * the prize is, and who pays to get there. Online events need no travel; a
 * hackathon abroad only belongs here when the organisers cover both travel and
 * accommodation. Past submission windows are dropped server-side.
 */

const STATUSES = ["All", "Open now", "Opening soon", "Upcoming event", "Rolling"] as const;
const BASE_LOCATIONS = ["Online", "Nairobi", "Kenya"];

export default function Hackathons({ embedded = false }: { embedded?: boolean }) {
  const { user } = useAuth();
  const queryClient = useQueryClient();

  const { data: watchedList } = useQuery({
    queryKey: ["watched-opps"],
    queryFn: getWatched,
    enabled: !!user,
  });
  const watchedUrls = new Set((watchedList ?? []).map((w) => w.opp_url));

  const toggleWatchMut = useMutation({
    mutationFn: async (args: {
      url: string;
      title: string;
      org: string;
      kind: string;
      deadline: string | null;
      opens: string | null;
      isWatched: boolean;
    }) => {
      if (args.isWatched) {
        return unwatchOpp(args.url);
      } else {
        if (isPushSupported()) {
          const key = await getVapidPublicKey();
          if (key) await subscribeToPush();
        }
        return watchOpp({
          url: args.url,
          title: args.title,
          org: args.org,
          kind: args.kind,
          deadline: args.deadline,
          opens: args.opens,
        });
      }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["watched-opps"] }),
  });

  const { data, isPending, error } = useQuery({
    queryKey: ["hackathons"],
    queryFn: api.hackathons,
    // Deadlines matter here, but the list itself changes rarely; the server
    // drops closed calls on every request.
    staleTime: 60 * 60_000,
    refetchOnWindowFocus: false,
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
    return items
      .filter(
        (o) =>
          (city === "All" || o.city === city) &&
          (statusFilter === "All" || status(o) === statusFilter) &&
          (tags.length === 0 || tags.some((t) => o.tags.includes(t))) &&
          (!q ||
            `${o.title} ${o.org} ${o.desc} ${o.tags.join(" ")}`.toLowerCase().includes(q)),
      )
      .sort((a, b) => deadline(a) - deadline(b));
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

  const openNow = items.filter((o) => status(o) === "Open now").length;
  const cash = items.filter((o) => o.stipend?.startsWith("Cash prize")).length;
  const online = items.filter((o) => o.city === "Online").length;

  const blurb = (
    <>
      Hackathons and build challenges with the prize and the travel cost stated
      upfront: {cash} carry a cash pool and {online} are fully online, so there
      is no flight or hotel to fund. Kenyan in-person events appear with their
      venue. A hackathon abroad is listed only when the organisers cover both
      travel and accommodation — never assume it, check the card.
    </>
  );

  return (
    <div className={`opp-page${embedded ? " opp-embedded" : ""}`}>
      {embedded ? (
        <div className="page-intro">
          <h2 className="page-title">Hackathons</h2>
          <p className="page-sub">{blurb}</p>
        </div>
      ) : (
        <>
          <PublicNav />
          <a className="skip-link" href="#main">Skip to content</a>
          <header className="opp-header">
            <div className="opp-header-inner">
              <h1>Hackathons</h1>
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
            placeholder="Search hackathons…"
            aria-label="Search hackathons"
          />

          <div className="opp-filter-group">
            <p className="opp-filter-label" id="hack-status">Application status</p>
            <div className="opp-chips" role="group" aria-labelledby="hack-status">
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
            <p className="opp-filter-label" id="hack-location">Location</p>
            <div className="opp-chips" role="group" aria-labelledby="hack-location">
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
            <p className="opp-filter-label" id="hack-interests">Prizes &amp; interests</p>
            <div className="opp-chips" role="group" aria-labelledby="hack-interests">
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
          {isPending && <Loading label="Loading hackathons" />}
          {error && <ErrorBox error={error} />}

          {data && !isPending && (
            <>
              <p className="opp-count">
                {results.length} hackathons · {openNow} open now · {cash} with a cash prize
              </p>

              {results.length === 0 && (
                <div className="opp-empty">Nothing matches yet. Try removing a filter.</div>
              )}

              <div className="opp-grid">
                {results.map((item) => (
                  <OpportunityCard
                    key={`${item.kind}:${item.title}`}
                    item={item}
                    isWatched={watchedUrls.has(item.url)}
                    onToggleWatch={
                      user
                        ? (url, title, org, kind, deadline, opens, isWatched) =>
                            toggleWatchMut.mutate({
                              url,
                              title,
                              org,
                              kind,
                              deadline,
                              opens,
                              isWatched,
                            })
                        : undefined
                    }
                  />
                ))}
              </div>
            </>
          )}
        </section>
      </main>
    </div>
  );
}
