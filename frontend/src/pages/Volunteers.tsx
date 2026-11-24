import { useMemo, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, getWatched, unwatchOpp, watchOpp, type Opportunities } from "../api";
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
 * The community screens: volunteer crew, speaker calls, open-source entry
 * points and mentorship. They share one layout, card and filter vocabulary —
 * only the kind, the endpoint and the copy differ — so they are one component
 * with four configurations rather than four near-copies that drift apart.
 *
 * Two kinds of entry can arrive: curated programmes whose crew call,
 * compensation or deadline was read off the organiser's own page, and — for
 * volunteer and speaker tabs — calls the live event calendars were already
 * advertising in an event description, tagged "From event feed".
 */

const STATUSES = ["All", "Open now", "Opening soon", "Upcoming event", "Rolling"] as const;
const BASE_LOCATIONS = ["Online", "Nairobi", "Kenya"];

type FeedConfig = {
  queryKey: string;
  load: () => Promise<Opportunities>;
  title: string;
  noun: string;
  placeholder: string;
  /** Only the volunteer and speaker tabs read the live event calendars. */
  live: boolean;
  intro: ReactNode;
};

const VOLUNTEER_FEED: FeedConfig = {
  queryKey: "volunteers",
  load: api.volunteers,
  title: "Volunteer",
  noun: "roles",
  placeholder: "Search volunteer roles…",
  live: true,
  intro: (
    <>
      Event crew and community roles at tech events — registration desks,
      session rooms, chapter organising and campus support. These are the ones
      no job board carries, so they are collected here from the organisers&apos;
      own pages, from the event calendars, and from a daily scan of published
      volunteer calls.
    </>
  ),
};

const SPEAKER_FEED: FeedConfig = {
  queryKey: "speakers",
  load: api.speakers,
  title: "Speakers",
  noun: "calls",
  placeholder: "Search calls for speakers…",
  live: true,
  intro: (
    <>
      Calls for papers and talks at conferences, meetups and DevFests. Each
      card carries the submission deadline, the talk format and — where the
      organisers publish it — whether travel is covered. Missing a CFP is
      usually a missed year, so these sort by the soonest deadline.
    </>
  ),
};

const OPEN_SOURCE_FEED: FeedConfig = {
  queryKey: "open-source",
  load: api.openSource,
  title: "Open source",
  noun: "projects",
  placeholder: "Search open-source entry points…",
  live: false,
  intro: (
    <>
      Contribution routes into open-source projects: mentored newcomer
      programmes, Kenyan and African projects with issues labelled for
      first-timers, and standing communities that review contributions. No
      visa, no travel, and the merged pull request is the artefact you keep.
    </>
  ),
};

const MENTORSHIP_FEED: FeedConfig = {
  queryKey: "mentorship",
  load: api.mentorship,
  title: "Mentorship",
  noun: "programmes",
  placeholder: "Search mentorship programmes…",
  live: false,
  intro: (
    <>
      Mentee intakes and calls for mentors. The first list is for getting a
      mentor on a structured programme; the second is the other direction —
      coaching a workshop or a newcomer, which is the cheapest way to build a
      public teaching record.
    </>
  ),
};

function CommunityFeed({ config, embedded = false }: { config: FeedConfig; embedded?: boolean }) {
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
    queryKey: [config.queryKey],
    queryFn: config.load,
    // Curated calls change rarely, and the backend caches the event half for a
    // day; do not refetch on every mount.
    staleTime: 12 * 60 * 60_000,
    refetchOnWindowFocus: false,
  });

  const items = data?.items ?? [];

  const [query, setQuery] = useState("");
  const [statusFilter, setStatusFilter] = useState("All");
  const [city, setCity] = useState("All");
  const [source, setSource] = useState("All");
  const [tags, setTags] = useState<string[]>([]);

  const locations = useMemo(() => {
    const extra = items.map((o) => o.city).filter((c) => c && !BASE_LOCATIONS.includes(c));
    return ["All", ...BASE_LOCATIONS, ...new Set(extra)];
  }, [items]);

  const sources = useMemo(() => ["All", ...new Set(items.map((o) => o.source).filter(Boolean))], [items]);

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
          (source === "All" || o.source === source) &&
          (statusFilter === "All" || status(o) === statusFilter) &&
          (tags.length === 0 || tags.some((t) => o.tags.includes(t))) &&
          (!q ||
            `${o.title} ${o.org} ${o.desc} ${o.tags.join(" ")}`.toLowerCase().includes(q)),
      )
      .sort((a, b) => deadline(a) - deadline(b));
  }, [items, query, city, source, tags, statusFilter]);

  const toggleTag = (tag: string) =>
    setTags((old) => (old.includes(tag) ? old.filter((t) => t !== tag) : [...old, tag]));

  const filtersOn = tags.length > 0 || city !== "All" || source !== "All" || statusFilter !== "All" || query !== "";
  const clearAll = () => {
    setTags([]);
    setCity("All");
    setSource("All");
    setQuery("");
    setStatusFilter("All");
  };

  const openNow = items.filter((o) => status(o) === "Open now").length;
  const fromFeed = items.filter((o) => o.tags.includes("From event feed")).length;

  const blurb = (
    <>
      {config.intro}{" "}
      {config.live && fromFeed > 0
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
          <h2 className="page-title">{config.title}</h2>
          <p className="page-sub">{blurb}</p>
        </div>
      ) : (
        <>
          <PublicNav />
          <a className="skip-link" href="#main">Skip to content</a>
          <header className="opp-header">
            <div className="opp-header-inner">
              <h1>{config.title}</h1>
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
            placeholder={config.placeholder}
            aria-label={config.placeholder.replace("…", "")}
          />

          <div className="opp-filter-group">
            <p className="opp-filter-label" id="community-status">Application status</p>
            <div className="opp-chips" role="group" aria-labelledby="community-status">
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
            <p className="opp-filter-label" id="community-location">Location</p>
            <div className="opp-chips" role="group" aria-labelledby="community-location">
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
            <p className="opp-filter-label" id="community-source">Source</p>
            <div className="opp-chips" role="group" aria-labelledby="community-source">
              {sources.map((s) => (
                <button
                  key={s}
                  type="button"
                  className="opp-chip"
                  aria-pressed={source === s}
                  onClick={() => setSource(s)}
                >
                  {s}
                </button>
              ))}
            </div>
          </div>

          <div className="opp-filter-group">
            <p className="opp-filter-label" id="community-roles">Roles</p>
            <div className="opp-chips" role="group" aria-labelledby="community-roles">
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
          {isPending && <Loading label={`Loading ${config.title.toLowerCase()}`} />}
          {error && <ErrorBox error={error} />}

          {data && !isPending && (
            <>
              {config.live && data.events_live === false && (
                <p className="opp-count">
                  The live event calendar could not be reached, so any calls we
                  picked up from it are missing. The curated roles below are
                  unaffected.
                </p>
              )}

              <p className="opp-count">
                {results.length} {config.noun} · {openNow} open now
                {config.live ? ` · ${fromFeed} from the event feed` : ""}
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

export default function Volunteers({ embedded = false }: { embedded?: boolean }) {
  return <CommunityFeed config={VOLUNTEER_FEED} embedded={embedded} />;
}

export function Speakers({ embedded = false }: { embedded?: boolean }) {
  return <CommunityFeed config={SPEAKER_FEED} embedded={embedded} />;
}

export function OpenSource({ embedded = false }: { embedded?: boolean }) {
  return <CommunityFeed config={OPEN_SOURCE_FEED} embedded={embedded} />;
}

export function Mentorship({ embedded = false }: { embedded?: boolean }) {
  return <CommunityFeed config={MENTORSHIP_FEED} embedded={embedded} />;
}
