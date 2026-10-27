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
 * Public Volunteer screen. Same card and filter vocabulary as the events and
 * internships pages, but the feed is volunteer crew, mentoring and speaking
 * slots only — the things no job board carries, and the ones that go stale
 * fastest if you miss the announcement.
 *
 * Two kinds of entry arrive here: curated programmes whose crew call and
 * compensation were read off the organiser's own site, and calls the live
 * event calendars were already advertising in an event description, tagged
 * "From event feed".
 */

const STATUSES = ["All", "Open now", "Opening soon", "Upcoming event", "Rolling"] as const;
const BASE_LOCATIONS = ["Online", "Nairobi", "Kenya"];

export default function Volunteers({ embedded = false }: { embedded?: boolean }) {
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
        });
      }
    },
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["watched-opps"] }),
  });

  const { data, isPending, error } = useQuery({
    queryKey: ["volunteers"],
    queryFn: api.volunteers,
    // Crew calls change rarely, and the backend caches the event half for a
    // day; do not refetch on every mount.
    staleTime: 12 * 60 * 60_000,
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
  const speaking = items.filter((o) => o.tags.includes("Speaker")).length;
  const fromFeed = items.filter((o) => o.tags.includes("From event feed")).length;

  const blurb = (
    <>
      Volunteer crew, mentoring and speaking slots at tech events — registration
      desks, session rooms, campus mentoring and call-for-speakers openings.
      These are the ones no job board carries, so they are collected here from
      the organisers' own pages and from the event calendars.{" "}
      {fromFeed > 0
        ? `${fromFeed} of them came straight off a live event listing.`
        : null}{" "}
      Deadlines taken from a previous cycle are estimates — always confirm on the
      official site.
    </>
  );

  return (
    <div className={`opp-page${embedded ? " opp-embedded" : ""}`}>
      {embedded ? (
        <div className="page-intro">
          <h2 className="page-title">Volunteer</h2>
          <p className="page-sub">{blurb}</p>
        </div>
      ) : (
        <>
          <PublicNav />
          <a className="skip-link" href="#main">Skip to content</a>
          <header className="opp-header">
            <div className="opp-header-inner">
              <h1>Volunteer</h1>
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
            placeholder="Search volunteer roles…"
            aria-label="Search volunteer roles"
          />

          <div className="opp-filter-group">
            <p className="opp-filter-label" id="vol-status">Application status</p>
            <div className="opp-chips" role="group" aria-labelledby="vol-status">
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
            <p className="opp-filter-label" id="vol-location">Location</p>
            <div className="opp-chips" role="group" aria-labelledby="vol-location">
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
            <p className="opp-filter-label" id="vol-roles">Roles</p>
            <div className="opp-chips" role="group" aria-labelledby="vol-roles">
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
          {isPending && <Loading label="Loading volunteer roles" />}
          {error && <ErrorBox error={error} />}

          {data && !isPending && (
            <>
              {data.events_live === false && (
                <p className="opp-count">
                  The live event calendar could not be reached, so any crew calls
                  we picked up from it are missing. The curated roles below are
                  unaffected.
                </p>
              )}

              <p className="opp-count">
                {results.length} roles · {openNow} open now · {speaking} speaker slots
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
                        ? (url, title, org, kind, deadline, isWatched) =>
                            toggleWatchMut.mutate({
                              url,
                              title,
                              org,
                              kind,
                              deadline,
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
