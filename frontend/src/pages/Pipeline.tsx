import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import {
  api,
  getTrackedOpps,
  setOpportunityStatus,
  type OpportunityStatus,
  type PipelineItem,
  type TrackedOpp,
} from "../api";
import { Empty, ErrorBox, TableSkeleton } from "../components";

type StageUpdate = Parameters<typeof api.setPipelineStage>[1];

const STAGE_LABELS: Record<string, string> = {
  "follow-up due": "Follow up",
  applied: "Applied",
  responded: "Replied",
  interview: "Interview",
  offer: "Offer",
  rejected: "Rejected",
  ghosted: "Ghosted",
};

const STAGE_CHIPS: Record<string, string> = {
  "follow-up due": "chip chip-warn",
  applied: "chip",
  responded: "chip chip-accent",
  interview: "chip chip-hit",
  offer: "chip chip-hit",
  rejected: "chip chip-miss",
  ghosted: "chip",
};

const CHIP_ORDER = [
  "follow-up due",
  "applied",
  "responded",
  "interview",
  "offer",
  "rejected",
  "ghosted",
];

const CLOSED = ["interview", "offer", "rejected", "ghosted"];

/** Shared action row: one click per stage, on both tables. */
function StageActions({
  stage,
  busy,
  onSet,
}: {
  stage: string;
  busy: boolean;
  onSet: (status: OpportunityStatus) => void;
}) {
  const closed = CLOSED.includes(stage);
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
      {!closed && (
        <button
          type="button"
          className="btn-ghost btn-sm"
          disabled={busy}
          title="They replied — no decision yet"
          onClick={() => onSet("heard_back")}
        >
          Replied
        </button>
      )}
      <button
        type="button"
        className="btn-ghost btn-sm"
        disabled={busy}
        onClick={() => onSet("interview")}
      >
        Interview
      </button>
      <button
        type="button"
        className="btn-ghost btn-sm"
        disabled={busy}
        onClick={() => onSet("offer")}
      >
        Offer
      </button>
      <button
        type="button"
        className="btn-ghost btn-sm"
        disabled={busy}
        onClick={() => onSet("rejected")}
      >
        Rejected
      </button>
      <button
        type="button"
        className="btn-ghost btn-sm"
        disabled={busy}
        title="No answer, and you have stopped expecting one"
        onClick={() => onSet("ghosted")}
      >
        Ghosted
      </button>
      {closed && (
        <button
          type="button"
          className="btn-ghost btn-sm"
          disabled={busy}
          title="Take the outcome back off and keep it waiting"
          onClick={() => onSet("")}
        >
          Reopen
        </button>
      )}
    </div>
  );
}

export default function Pipeline() {
  const qc = useQueryClient();
  const [announcement, setAnnouncement] = useState("");

  const { data, isPending, error } = useQuery({
    queryKey: ["pipeline"],
    queryFn: api.pipeline,
  });

  const trackedQuery = useQuery({
    queryKey: ["tracked-opps"],
    queryFn: getTrackedOpps,
  });

  const update = useMutation({
    mutationFn: ({ id, body }: { id: number; body: StageUpdate }) =>
      api.setPipelineStage(id, body),
    onSuccess: (item, variables) => {
      void qc.invalidateQueries({ queryKey: ["pipeline"] });
      void qc.invalidateQueries({ queryKey: ["applications"] });
      // Recorded in the row's own words so a screen reader user hears the
      // change even though the row is re-rendered rather than kept in place.
      setAnnouncement(
        `${variables.id === item.job_id ? item.title : "Application"} moved to ${
          STAGE_LABELS[item.stage] ?? item.stage
        }.`,
      );
    },
  });

  const trackOpp = useMutation({
    mutationFn: ({ row, status }: { row: TrackedOpp; status: OpportunityStatus }) =>
      setOpportunityStatus({
        url: row.opp_url,
        title: row.opp_title,
        org: row.opp_org,
        kind: row.opp_kind,
        deadline: row.deadline,
        status,
      }),
    onSuccess: (row) => {
      void qc.invalidateQueries({ queryKey: ["tracked-opps"] });
      setAnnouncement(
        `${row.opp_title} moved to ${STAGE_LABELS[row.stage] ?? row.stage}.`,
      );
    },
  });

  if (isPending) return <TableSkeleton cols={5} />;
  if (error) return <ErrorBox error={error} />;

  const items = data?.items ?? [];
  const tracked = trackedQuery.data ?? [];
  const sent = items.length;
  const open = items.filter((item) => !CLOSED.includes(item.stage)).length;

  const act = (item: PipelineItem, body: StageUpdate) =>
    update.mutate({ id: item.job_id, body });

  return (
    <>
      <div className="page-intro">
        <h2 className="page-title">Pipeline</h2>
        <p className="page-sub">
          <span className="material-symbols-outlined" aria-hidden="true">view_kanban</span>
          {sent === 0
            ? "What came back after you applied"
            : `${sent} sent · ${open} still waiting on a reply`}
          {tracked.length > 0 && ` · ${tracked.length} opportunit${tracked.length === 1 ? "y" : "ies"} tracked`}
        </p>
      </div>

      <span className="sr-only" role="status" aria-live="polite">{announcement}</span>

      {update.isError && <ErrorBox error={update.error} />}
      {trackOpp.isError && <ErrorBox error={trackOpp.error} />}

      {items.length === 0 && tracked.length === 0 ? (
        <Empty
          title="No applications yet"
          hint={
            <>
              Applications you submit — or the agent submits for you — appear here so you
              can record replies and outcomes, and cards on the Internships, Programs and
              Volunteer tabs can be marked applied. Prepared job applications wait in the{" "}
              <Link to="/review">review queue</Link>.
            </>
          }
        />
      ) : (
        <>
          {items.length > 0 && (
            <>
              <div className="chips" style={{ marginBottom: 12 }}>
                {CHIP_ORDER.filter((stage) => (data?.counts[stage] ?? 0) > 0).map((stage) => (
                  <span key={stage} className={STAGE_CHIPS[stage] ?? "chip"}>
                    {STAGE_LABELS[stage] ?? stage} {data?.counts[stage] ?? 0}
                  </span>
                ))}
              </div>

              <div className="table-wrap table-responsive-apps">
                <table>
                  <caption className="sr-only">
                    {sent} applications, oldest waiting first
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col" style={{ width: 110 }}>Stage</th>
                      <th scope="col">Role</th>
                      <th scope="col" style={{ width: 160 }}>Company</th>
                      <th scope="col" style={{ width: 70 }}>Waiting</th>
                      <th scope="col">Next step</th>
                    </tr>
                  </thead>
                  <tbody>
                    {items.map((item) => {
                      const closed = CLOSED.includes(item.stage);
                      const busy = update.isPending && update.variables?.id === item.job_id;
                      return (
                        <tr key={item.job_id}>
                          <td>
                            <span className={STAGE_CHIPS[item.stage] ?? "chip"}>
                              {STAGE_LABELS[item.stage] ?? item.stage}
                            </span>
                          </td>
                          <td className="col-role">
                            <Link to={`/job/${item.job_id}`} className="cell-title">
                              {item.title}
                            </Link>
                          </td>
                          <td className="col-company">{item.company}</td>
                          <td className="cell-dim">
                            {item.days_waiting === null
                              ? "—"
                              : `${item.days_waiting}d${
                                  item.follow_up_overdue ? " · overdue" : ""
                                }`}
                          </td>
                          <td>
                            <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
                              {!closed && (
                                <button
                                  type="button"
                                  className="btn-ghost btn-sm"
                                  disabled={busy}
                                  title="They replied — no interview decision yet"
                                  onClick={() => act(item, { response_received: true })}
                                >
                                  Replied
                                </button>
                              )}
                              <button
                                type="button"
                                className="btn-ghost btn-sm"
                                disabled={busy}
                                onClick={() => act(item, { outcome: "interview" })}
                              >
                                Interview
                              </button>
                              <button
                                type="button"
                                className="btn-ghost btn-sm"
                                disabled={busy}
                                onClick={() => act(item, { outcome: "offer" })}
                              >
                                Offer
                              </button>
                              <button
                                type="button"
                                className="btn-ghost btn-sm"
                                disabled={busy}
                                onClick={() => act(item, { outcome: "rejected" })}
                              >
                                Rejected
                              </button>
                              <button
                                type="button"
                                className="btn-ghost btn-sm"
                                disabled={busy}
                                title="No answer, and you have stopped expecting one"
                                onClick={() => act(item, { outcome: "ghosted" })}
                              >
                                Ghosted
                              </button>
                              {item.follow_up_due ? (
                                <button
                                  type="button"
                                  className="btn-ghost btn-sm"
                                  disabled={busy}
                                  onClick={() => act(item, { follow_up_in_days: 0 })}
                                >
                                  Clear follow-up
                                </button>
                              ) : (
                                !closed && (
                                  <button
                                    type="button"
                                    className="btn-ghost btn-sm"
                                    disabled={busy}
                                    onClick={() => act(item, { follow_up_in_days: 5 })}
                                  >
                                    Follow up in 5d
                                  </button>
                                )
                              )}
                              {closed && (
                                <button
                                  type="button"
                                  className="btn-ghost btn-sm"
                                  disabled={busy}
                                  title="Take the outcome back off and keep it waiting"
                                  onClick={() => act(item, { outcome: "" })}
                                >
                                  Reopen
                                </button>
                              )}
                            </div>
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </>
          )}

          {tracked.length > 0 && (
            <section style={{ marginTop: items.length > 0 ? 30 : 0 }}>
              <h3 className="page-title" style={{ fontSize: 18 }}>Opportunities</h3>
              <p className="page-sub" style={{ marginBottom: 10 }}>
                Internships, programs and volunteer slots you marked applied on their cards.
              </p>
              <div className="table-wrap table-responsive-apps">
                <table>
                  <caption className="sr-only">
                    {tracked.length} tracked opportunities
                  </caption>
                  <thead>
                    <tr>
                      <th scope="col" style={{ width: 110 }}>Stage</th>
                      <th scope="col">Opportunity</th>
                      <th scope="col" style={{ width: 180 }}>Organisation</th>
                      <th scope="col">Next step</th>
                    </tr>
                  </thead>
                  <tbody>
                    {tracked.map((row) => {
                      const busy =
                        trackOpp.isPending && trackOpp.variables?.row.opp_url === row.opp_url;
                      return (
                        <tr key={row.opp_url}>
                          <td>
                            <span className={STAGE_CHIPS[row.stage] ?? "chip"}>
                              {STAGE_LABELS[row.stage] ?? row.stage}
                            </span>
                          </td>
                          <td className="col-role">
                            <a
                              href={row.opp_url}
                              target="_blank"
                              rel="noreferrer"
                              className="cell-title"
                            >
                              {row.opp_title}
                            </a>
                          </td>
                          <td className="col-company">{row.opp_org}</td>
                          <td>
                            <StageActions
                              stage={row.stage}
                              busy={busy}
                              onSet={(status) => trackOpp.mutate({ row, status })}
                            />
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </section>
          )}
        </>
      )}
    </>
  );
}
