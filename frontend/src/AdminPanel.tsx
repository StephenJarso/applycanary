import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type FeedbackItem } from "./api";
import { ErrorBox, Loading } from "./components";

export function AdminPanel() {
  const qc = useQueryClient();
  const [activeTab, setActiveTab] = useState<"services" | "users" | "feedback">("services");
  const [feedbackStatus, setFeedbackStatus] = useState<string>("all");
  const [testedProviders, setTestedProviders] = useState<Record<string, { ok: boolean; msg: string }>>({});
  const [editingNotes, setEditingNotes] = useState<Record<number, string>>({});

  const system = useQuery({
    queryKey: ["admin", "system-status"],
    queryFn: api.systemStatus,
    refetchInterval: 30_000,
  });

  const feedbackQuery = useQuery({
    queryKey: ["admin", "feedback", feedbackStatus],
    queryFn: () => api.adminFeedback(feedbackStatus),
    refetchInterval: 30_000,
  });

  const testLlm = useMutation({
    mutationFn: (provider: string) => api.testLlm(provider),
    onSuccess: (data, provider) => {
      setTestedProviders((prev) => ({
        ...prev,
        [provider]: data.ok
          ? { ok: true, msg: `✓ Working (${data.latency_ms}ms) · ${data.model || ""}` }
          : { ok: false, msg: `✗ Failed: ${data.error || "Unknown error"}` },
      }));
    },
    onError: (err, provider) => {
      setTestedProviders((prev) => ({
        ...prev,
        [provider]: { ok: false, msg: `✗ Probe error: ${err instanceof Error ? err.message : String(err)}` },
      }));
    },
  });

  const updateFeedback = useMutation({
    mutationFn: ({ id, status, admin_notes }: { id: number; status?: string; admin_notes?: string }) =>
      api.updateFeedback(id, { status, admin_notes }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin", "feedback"] });
      void qc.invalidateQueries({ queryKey: ["admin", "system-status"] });
    },
  });

  const deleteFeedback = useMutation({
    mutationFn: (id: number) => api.deleteFeedback(id),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin", "feedback"] });
      void qc.invalidateQueries({ queryKey: ["admin", "system-status"] });
    },
  });

  const triggerJob = useMutation({
    mutationFn: (jobName: string) => api.triggerJob(jobName),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin", "system-status"] });
    },
  });

  const embedAll = useMutation({
    mutationFn: () => api.embedAll(100),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin", "system-status"] });
    },
  });

  if (system.isPending) return <Loading label="Loading admin telemetry" />;
  if (system.isError) return <ErrorBox error={system.error} />;

  const data = system.data;
  const feedbackData = feedbackQuery.data;

  return (
    <div className="card" style={{ borderColor: "var(--ac-primary, #6366f1)", borderWidth: 2 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 10, marginBottom: 16 }}>
        <div>
          <h3 className="card-title" style={{ margin: 0, display: "flex", alignItems: "center", gap: 8 }}>
            <span className="material-symbols-outlined" style={{ color: "var(--ac-primary)" }}>shield_person</span>
            Administrator Control &amp; System Health
          </h3>
          <p className="cell-dim" style={{ margin: "4px 0 0", fontSize: 13 }}>
            Live status of database, LLM provider keys, background workers, and user inquiries.
          </p>
        </div>
        <div style={{ display: "flex", gap: 6 }}>
          <button
            type="button"
            className={activeTab === "services" ? "btn-primary" : "btn-ghost"}
            onClick={() => setActiveTab("services")}
            style={{ fontSize: 13, padding: "6px 12px" }}
          >
            Services &amp; LLMs
          </button>
          <button
            type="button"
            className={activeTab === "users" ? "btn-primary" : "btn-ghost"}
            onClick={() => setActiveTab("users")}
            style={{ fontSize: 13, padding: "6px 12px" }}
          >
            User Metrics
          </button>
          <button
            type="button"
            className={activeTab === "feedback" ? "btn-primary" : "btn-ghost"}
            onClick={() => setActiveTab("feedback")}
            style={{ fontSize: 13, padding: "6px 12px" }}
          >
            Feedback Inbox
            {data?.database.feedback_new ? (
              <span className="chip chip-hit" style={{ marginLeft: 6, padding: "2px 6px" }}>
                {data.database.feedback_new} new
              </span>
            ) : null}
          </button>
        </div>
      </div>

      {/* Tab 1: Services & LLMs */}
      {activeTab === "services" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Service Status Row */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
            <div className="card" style={{ background: "var(--bg-raised, #f8fafc)", padding: 12, margin: 0 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <strong>Database</strong>
                <span className="chip chip-hit">Connected</span>
              </div>
              <div className="cell-dim" style={{ fontSize: 12, marginTop: 6 }}>
                Dialect: {data.database.dialect} · Vector: {data.database.vector_backend}
              </div>
            </div>

            <div className="card" style={{ background: "var(--bg-raised, #f8fafc)", padding: 12, margin: 0 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <strong>Scheduler</strong>
                <span className={`chip ${data.scheduler.running ? "chip-hit" : "chip-miss"}`}>
                  {data.scheduler.running ? "Running" : "Stopped"}
                </span>
              </div>
              <div className="cell-dim" style={{ fontSize: 12, marginTop: 6 }}>
                {data.scheduler.jobs.length} jobs registered
              </div>
            </div>

            <div className="card" style={{ background: "var(--bg-raised, #f8fafc)", padding: 12, margin: 0 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <strong>Email Delivery</strong>
                <span className={`chip ${data.email.configured ? "chip-hit" : "chip-miss"}`}>
                  {data.email.configured ? data.email.backend : "Disabled"}
                </span>
              </div>
              <div className="cell-dim" style={{ fontSize: 12, marginTop: 6 }}>
                Backend: {data.email.backend}
              </div>
            </div>
          </div>

          {/* LLM Providers */}
          <div>
            <h4 style={{ margin: "10px 0 8px", fontSize: 14 }}>
              LLM Providers &amp; Key Status
              <span className="cell-dim" style={{ fontWeight: "normal", fontSize: 12, marginLeft: 8 }}>
                (Active: <strong>{data.llm.active_provider || "None"}</strong>)
              </span>
            </h4>
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {data.llm.providers.map((p) => {
                const test = testedProviders[p.key];
                return (
                  <div
                    key={p.key}
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      alignItems: "center",
                      flexWrap: "wrap",
                      gap: 8,
                      padding: "10px 14px",
                      borderRadius: "var(--radius)",
                      border: "1px solid var(--border)",
                      background: p.is_active ? "color-mix(in srgb, var(--ac-primary, #6366f1) 5%, transparent)" : undefined,
                    }}
                  >
                    <div>
                      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                        <strong>{p.name}</strong>
                        {p.is_active && <span className="chip chip-hit">Primary</span>}
                        {p.configured ? (
                          <span className="chip" style={{ fontSize: 11 }}>Configured</span>
                        ) : (
                          <span className="chip chip-miss" style={{ fontSize: 11 }}>No API key</span>
                        )}
                        {p.on_cooldown && <span className="chip chip-miss">Cooling down / failing</span>}
                      </div>
                      <div className="cell-dim" style={{ fontSize: 12, marginTop: 4 }}>
                        Default Model: <code>{p.model || "default"}</code>
                      </div>
                      {test && (
                        <div
                          style={{
                            fontSize: 12,
                            marginTop: 4,
                            color: test.ok ? "var(--ok, #16a34a)" : "var(--bad, #dc2626)",
                            fontWeight: 500,
                          }}
                        >
                          {test.msg}
                        </div>
                      )}
                    </div>
                    <div>
                      <button
                        type="button"
                        className="btn-ghost"
                        style={{ fontSize: 12, padding: "4px 10px" }}
                        disabled={testLlm.isPending || !p.configured}
                        onClick={() => testLlm.mutate(p.key)}
                        title={!p.configured ? "Cannot test without an API key" : "Send probe request to test credentials"}
                      >
                        {testLlm.isPending ? "Probing…" : "Test API Key"}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>
          </div>

          {/* Quick Actions / Trigger Jobs */}
          <div>
            <h4 style={{ margin: "12px 0 8px", fontSize: 14 }}>Background Tasks &amp; Manual Triggers</h4>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => triggerJob.mutate("poll_curated")}
                disabled={triggerJob.isPending}
              >
                Trigger Curated Poll
              </button>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => triggerJob.mutate("poll_broad")}
                disabled={triggerJob.isPending}
              >
                Trigger Broad Poll
              </button>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => triggerJob.mutate("score_new")}
                disabled={triggerJob.isPending}
              >
                Trigger Score Jobs
              </button>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => triggerJob.mutate("refresh_opportunities")}
                disabled={triggerJob.isPending}
              >
                Refresh Opportunities
              </button>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => embedAll.mutate()}
                disabled={embedAll.isPending}
              >
                {embedAll.isPending ? <span className="spinner" /> : "Backfill Vector Embeddings"}
              </button>
            </div>
            {triggerJob.isSuccess && (
              <div className="banner banner-ok" role="status" style={{ marginTop: 8 }}>
                {triggerJob.data.message}
              </div>
            )}
            {embedAll.isSuccess && (
              <div className="banner banner-ok" role="status" style={{ marginTop: 8 }}>
                Embedded {embedAll.data.embedded} new postings into the vector index.
              </div>
            )}
          </div>

          {/* Connectors Status */}
          <div>
            <h4 style={{ margin: "12px 0 8px", fontSize: 14 }}>Job Connector Health</h4>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Connector</th>
                    <th>Status</th>
                    <th>Last Run</th>
                    <th>Jobs Found</th>
                    <th>New</th>
                  </tr>
                </thead>
                <tbody>
                  {data.connectors.map((c) => (
                    <tr key={c.source}>
                      <td><strong>{c.source}</strong></td>
                      <td>
                        <span className={`chip ${c.ok ? "chip-hit" : "chip-miss"}`}>
                          {c.ok ? "OK" : "Error"}
                        </span>
                        {c.error && c.error !== "No runs recorded yet" && (
                          <div className="cell-dim" style={{ fontSize: 11, color: "var(--bad)" }}>
                            {c.error}
                          </div>
                        )}
                      </td>
                      <td>{c.last_run ? new Date(c.last_run).toLocaleString() : "Never"}</td>
                      <td>{c.found}</td>
                      <td>{c.new_jobs}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Tab 2: User Metrics */}
      {activeTab === "users" && (
        <div>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 12 }}>
            <div className="ac-metric">
              <div className="ac-metric-value">{data.users.total}</div>
              <div className="ac-metric-label">Total Users</div>
            </div>
            <div className="ac-metric">
              <div className="ac-metric-value">{data.users.active}</div>
              <div className="ac-metric-label">Active Users</div>
            </div>
            <div className="ac-metric">
              <div className="ac-metric-value">{data.users.verified}</div>
              <div className="ac-metric-label">Verified Emails</div>
            </div>
            <div className="ac-metric">
              <div className="ac-metric-value">{data.users.admin}</div>
              <div className="ac-metric-label">Admins</div>
            </div>
            <div className="ac-metric">
              <div className="ac-metric-value">{data.database.jobs_count}</div>
              <div className="ac-metric-label">Live Postings</div>
            </div>
            <div className="ac-metric">
              <div className="ac-metric-value">{data.database.applications_count}</div>
              <div className="ac-metric-label">Applications</div>
            </div>
            <div className="ac-metric">
              <div className="ac-metric-value">{data.database.sessions_count}</div>
              <div className="ac-metric-label">Mock Interviews</div>
            </div>
          </div>
        </div>
      )}

      {/* Tab 3: Feedback Inbox */}
      {activeTab === "feedback" && (
        <div>
          <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12, flexWrap: "wrap", gap: 8 }}>
            <div style={{ display: "flex", gap: 6 }}>
              {(["all", "new", "in_progress", "resolved"] as const).map((st) => (
                <button
                  key={st}
                  type="button"
                  className={feedbackStatus === st ? "btn-primary" : "btn-ghost"}
                  onClick={() => setFeedbackStatus(st)}
                  style={{ fontSize: 12, padding: "4px 10px", textTransform: "capitalize" }}
                >
                  {st.replace("_", " ")}
                </button>
              ))}
            </div>
            <span className="cell-dim" style={{ fontSize: 13 }}>
              Total: {feedbackData?.total ?? 0} ({feedbackData?.new_count ?? 0} new)
            </span>
          </div>

          {feedbackQuery.isPending ? (
            <Loading label="Loading feedback" />
          ) : !feedbackData?.items.length ? (
            <div className="empty" style={{ padding: "28px 12px" }}>
              <div className="empty-title">No feedback entries found</div>
              <div className="cell-dim">User inquiries and contact messages will appear here.</div>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              {feedbackData.items.map((fb: FeedbackItem) => (
                <div
                  key={fb.id}
                  className="card"
                  style={{
                    background: fb.status === "new" ? "color-mix(in srgb, var(--ac-primary, #6366f1) 4%, transparent)" : undefined,
                    padding: 16,
                    margin: 0,
                  }}
                >
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8, marginBottom: 8 }}>
                    <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <strong>{fb.name}</strong>
                      {fb.email ? (
                        <a href={`mailto:${fb.email}`} className="cell-dim" style={{ fontSize: 12 }}>
                          {fb.email}
                        </a>
                      ) : (
                        <span className="cell-dim" style={{ fontSize: 12 }}>(no email provided)</span>
                      )}
                      <span className="chip" style={{ fontSize: 11, textTransform: "capitalize" }}>
                        {fb.category}
                      </span>
                      <span
                        className={`chip ${fb.status === "new" ? "chip-hit" : fb.status === "resolved" ? "" : "chip-warn"}`}
                        style={{ fontSize: 11, textTransform: "capitalize" }}
                      >
                        {fb.status.replace("_", " ")}
                      </span>
                    </div>
                    <span className="cell-dim" style={{ fontSize: 12 }}>
                      {new Date(fb.created_at).toLocaleString()}
                    </span>
                  </div>

                  <p style={{ whiteSpace: "pre-wrap", margin: "8px 0 12px", fontSize: 13.5 }}>
                    {fb.message}
                  </p>

                  {/* Admin notes & status actions */}
                  <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 8, paddingTop: 8, borderTop: "1px solid var(--border)" }}>
                    <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                      {fb.status !== "in_progress" && (
                        <button
                          type="button"
                          className="btn-ghost"
                          style={{ fontSize: 11, padding: "2px 8px" }}
                          onClick={() => updateFeedback.mutate({ id: fb.id, status: "in_progress" })}
                        >
                          Mark In Progress
                        </button>
                      )}
                      {fb.status !== "resolved" && (
                        <button
                          type="button"
                          className="btn-ghost"
                          style={{ fontSize: 11, padding: "2px 8px" }}
                          onClick={() => updateFeedback.mutate({ id: fb.id, status: "resolved" })}
                        >
                          Mark Resolved
                        </button>
                      )}
                      {fb.status === "resolved" && (
                        <button
                          type="button"
                          className="btn-ghost"
                          style={{ fontSize: 11, padding: "2px 8px" }}
                          onClick={() => updateFeedback.mutate({ id: fb.id, status: "new" })}
                        >
                          Reopen
                        </button>
                      )}
                      <button
                        type="button"
                        className="btn-ghost"
                        style={{ fontSize: 11, padding: "2px 8px", color: "var(--bad)" }}
                        onClick={() => {
                          if (confirm("Delete this feedback entry?")) deleteFeedback.mutate(fb.id);
                        }}
                      >
                        Delete
                      </button>
                    </div>

                    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
                      <input
                        type="text"
                        placeholder="Admin notes…"
                        value={editingNotes[fb.id] ?? fb.admin_notes ?? ""}
                        onChange={(e) => setEditingNotes({ ...editingNotes, [fb.id]: e.target.value })}
                        style={{ fontSize: 12, padding: "3px 8px", minWidth: 160 }}
                      />
                      {editingNotes[fb.id] !== undefined && editingNotes[fb.id] !== fb.admin_notes && (
                        <button
                          type="button"
                          className="btn-ghost"
                          style={{ fontSize: 11, padding: "2px 8px" }}
                          onClick={() => updateFeedback.mutate({ id: fb.id, admin_notes: editingNotes[fb.id] })}
                        >
                          Save
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
