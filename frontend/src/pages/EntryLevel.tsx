import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type EntryLevelRole } from "../api";
import { ErrorBox, Loading, relTime } from "../components";
import { daysUntil, fmtDate } from "../OpportunityCard";

/**
 * Entry-level technology roles, live from work.ke.
 *
 * work.ke is a Kenyan board that curates junior vacancies and links each one to
 * the employer's own advert instead of collecting applications. That makes it a
 * better starting point than a broad keyword feed for a first IT role, and it
 * keeps the "where does this actually apply?" question answerable before the
 * user invests time in a tailored CV.
 *
 * Nothing here is scored or tailored: the cards are a discovery surface, and
 * the same roles also enter the normal feed through the `workke` source when
 * the agent polls.
 */

function closingText(role: EntryLevelRole): string {
  if (!role.closing_at) {
    return role.closing_label || "Deadline not published by the employer";
  }
  const days = daysUntil(role.closing_at);
  if (days < 0) return `Closed ${fmtDate(role.closing_at)}`;
  if (days === 0) return "Closes today";
  return `Closes ${fmtDate(role.closing_at)} · ${days} day${days === 1 ? "" : "s"} left`;
}

export default function EntryLevel() {
  const queryClient = useQueryClient();
  const { data, isPending, error } = useQuery({
    queryKey: ["entry-level"],
    queryFn: () => api.entryLevel(),
    staleTime: 10 * 60_000,
    refetchOnWindowFocus: false,
  });
  const refresh = useMutation({
    mutationFn: () => api.entryLevel(true),
    onSuccess: (feed) => queryClient.setQueryData(["entry-level"], feed),
  });

  const [query, setQuery] = useState("");
  const [employerType, setEmployerType] = useState("All");
  const [remoteOnly, setRemoteOnly] = useState(false);
  const [closingSoon, setClosingSoon] = useState(false);

  const items = data?.roles ?? [];
  const employerTypes = useMemo(
    () => [...new Set(items.map((role) => role.employer_type).filter(Boolean))],
    [items],
  );

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    return items.filter((role) => {
      if (employerType !== "All" && role.employer_type !== employerType) return false;
      if (remoteOnly && !role.is_remote) return false;
      if (closingSoon) {
        if (!role.closing_at) return false;
        const days = daysUntil(role.closing_at);
        if (days < 0 || days > 7) return false;
      }
      if (!q) return true;
      return `${role.title} ${role.employer} ${role.location} ${role.category} ${role.summary}`
        .toLowerCase()
        .includes(q);
    });
  }, [items, query, employerType, remoteOnly, closingSoon]);

  const remoteCount = items.filter((role) => role.is_remote).length;
  const datedCount = items.filter((role) => role.closing_at).length;
  const filtersOn = query !== "" || employerType !== "All" || remoteOnly || closingSoon;

  return (
    <div className="opp-page opp-embedded">
      <div className="page-intro">
        <h2 className="page-title">Entry level</h2>
        <p className="page-sub">
          Entry-level IT and tech roles from{" "}
          <a
            href="https://work.ke/jobs/entry-level-technology"
            target="_blank"
            rel="noreferrer"
          >
            work.ke
          </a>
          , each linked to the employer&apos;s own advert. work.ke does not collect
          applications and never charges job seekers — confirm the full role on the
          employer&apos;s page before applying.
        </p>
      </div>

      <main className="opp-layout">
        <aside className="opp-filters" aria-label="Filters">
          <div className="opp-filter-group">
            <input
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="Search entry-level roles…"
              aria-label="Search entry-level roles"
            />
          </div>

          <div className="opp-filter-group">
            <p className="opp-filter-label" id="entry-work">
              Work arrangement
            </p>
            <div className="opp-chips" role="group" aria-labelledby="entry-work">
              <button
                type="button"
                className="opp-chip"
                aria-pressed={remoteOnly}
                onClick={() => setRemoteOnly((on) => !on)}
              >
                Remote only
              </button>
              <button
                type="button"
                className="opp-chip"
                aria-pressed={closingSoon}
                onClick={() => setClosingSoon((on) => !on)}
              >
                Closing this week
              </button>
            </div>
          </div>

          {employerTypes.length > 1 && (
            <div className="opp-filter-group">
              <p className="opp-filter-label" id="entry-source">
                Listed by
              </p>
              <div className="opp-chips" role="group" aria-labelledby="entry-source">
                {["All", ...employerTypes].map((type) => (
                  <button
                    key={type}
                    type="button"
                    className="opp-chip"
                    aria-pressed={employerType === type}
                    onClick={() => setEmployerType(type)}
                  >
                    {type}
                  </button>
                ))}
              </div>
            </div>
          )}

          {filtersOn && (
            <button
              type="button"
              className="opp-clear"
              onClick={() => {
                setQuery("");
                setEmployerType("All");
                setRemoteOnly(false);
                setClosingSoon(false);
              }}
            >
              Clear filters
            </button>
          )}

          <p className="entry-fineprint">
            Role summaries are work.ke&apos;s plain-English rewrite of the
            employer&apos;s advert. The employer&apos;s own page is the source of
            truth for pay, requirements and deadlines.
          </p>
        </aside>

        <section className="opp-results" aria-live="polite">
          {isPending && <Loading label="Fetching entry-level roles from work.ke" />}
          {error && <ErrorBox error={error} />}

          {data && (
            <>
              <div className="entry-toolbar">
                <p className="opp-count">
                  {results.length} role{results.length === 1 ? "" : "s"} ·{" "}
                  {remoteCount} remote · {datedCount} with a published deadline
                  {data.fetched_at ? ` · updated ${relTime(data.fetched_at)} ago` : ""}
                </p>
                <button
                  type="button"
                  className="btn btn-ghost btn-sm"
                  onClick={() => refresh.mutate()}
                  disabled={refresh.isPending}
                  title="Ask work.ke for the current list"
                >
                  {refresh.isPending ? (
                    <span className="spinner" />
                  ) : (
                    <span className="material-symbols-outlined" aria-hidden="true">sync</span>
                  )}
                  Refresh
                </button>
              </div>

              {data.warnings.length > 0 && (
                <div className="banner banner-warn" role="status">
                  Some work.ke pages could not be refreshed, so this list may be
                  missing roles.
                </div>
              )}
              {refresh.isError && (
                <div className="banner banner-bad" role="alert">
                  {String(refresh.error)}
                </div>
              )}

              {results.length === 0 && (
                <div className="opp-empty">
                  {items.length === 0
                    ? "work.ke has no entry-level technology roles open right now. Try Refresh in a few hours."
                    : "Nothing matches these filters. Try removing one."}
                </div>
              )}

              <div className="opp-grid">
                {results.map((role) => {
                  const soon =
                    role.closing_at !== null &&
                    daysUntil(role.closing_at) >= 0 &&
                    daysUntil(role.closing_at) <= 7;
                  const onWorkKe = role.apply_url === role.source_url;
                  return (
                    <article key={role.slug} className="opp-card">
                      <div className="opp-card-top">
                        <span className="opp-kind opp-kind-intern">
                          {role.seniority || "Entry level"}
                        </span>
                        <span className="opp-source">
                          {role.employer_type || "via work.ke"}
                          {role.agency ? ` · ${role.agency}` : ""}
                        </span>
                      </div>

                      <div>
                        <h3>{role.title}</h3>
                        <p className="opp-meta">
                          {role.employer} · {role.location}
                          {role.is_remote && " · Remote"}
                        </p>
                      </div>

                      {role.summary && (
                        <p className="opp-desc entry-summary">{role.summary}</p>
                      )}

                      <div
                        className={`opp-timeline${soon ? " entry-deadline-soon" : ""}`}
                      >
                        <p>{closingText(role)}</p>
                      </div>

                      <div className="opp-tags">
                        {role.category && (
                          <span className="opp-tag">{role.category}</span>
                        )}
                        {role.work_mode && role.work_mode !== "Not stated" && (
                          <span className="opp-tag">{role.work_mode}</span>
                        )}
                        {role.salary_disclosed && (
                          <span className="opp-tag">Salary disclosed</span>
                        )}
                      </div>

                      <div className="entry-actions">
                        <a
                          className="btn btn-ai"
                          href={role.apply_url}
                          target="_blank"
                          rel="noreferrer"
                        >
                          {onWorkKe
                            ? "Open the advert on work.ke"
                            : "Apply on employer site ↗"}
                        </a>
                        {!onWorkKe && (
                          <a
                            className="btn btn-ghost"
                            href={role.source_url}
                            target="_blank"
                            rel="noreferrer"
                          >
                            View on work.ke
                          </a>
                        )}
                      </div>
                    </article>
                  );
                })}
              </div>
            </>
          )}
        </section>
      </main>
    </div>
  );
}
