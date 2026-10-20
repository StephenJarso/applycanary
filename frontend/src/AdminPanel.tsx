import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type AdminUserItem, type FeedbackItem } from "./api";
import { ErrorBox, Loading } from "./components";

function relTime(iso: string | null): string {
  if (!iso) return "Never";
  const d = Math.floor((Date.now() - new Date(iso).getTime()) / 86400000);
  if (d === 0) return "Today";
  if (d === 1) return "Yesterday";
  if (d < 30) return `${d}d ago`;
  if (d < 365) return `${Math.floor(d / 30)}mo ago`;
  return `${Math.floor(d / 365)}y ago`;
}

export function AdminPanel() {
  const qc = useQueryClient();
  const [activeTab, setActiveTab] = useState<"services" | "users" | "feedback" | "sources">("services");
  const [feedbackStatus, setFeedbackStatus] = useState<string>("all");
  const [testedProviders, setTestedProviders] = useState<Record<string, { ok: boolean; msg: string }>>({});
  const [editingNotes, setEditingNotes] = useState<Record<number, string>>({});

  // LLM keys form state
  const [showKeyForm, setShowKeyForm] = useState(false);
  const [llmKeys, setLlmKeys] = useState<Record<string, string>>({
    xai: "",
    gemini: "",
    openrouter: "",
    groq: "",
    anthropic: "",
    openai: "",
  });
  const [keyUpdateMsg, setKeyUpdateMsg] = useState("");

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

  const sourceRunsQuery = useQuery({
    queryKey: ["admin", "source-runs"],
    queryFn: () => api.getSourceRuns(50),
    enabled: activeTab === "sources",
    refetchInterval: 20_000,
  });

  const invitesQuery = useQuery({
    queryKey: ["admin", "invites"],
    queryFn: api.getInvites,
    enabled: activeTab === "users",
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

  const updateLlmKeysMut = useMutation({
    mutationFn: (keys: Record<string, string>) => api.updateLlmKeys(keys),
    onSuccess: (res) => {
      setKeyUpdateMsg(`✓ Updated providers: ${res.updated.join(", ") || "none"}. ${res.note}`);
      void qc.invalidateQueries({ queryKey: ["admin", "system-status"] });
    },
    onError: (err) => {
      setKeyUpdateMsg(`✗ Update failed: ${err instanceof Error ? err.message : String(err)}`);
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

  const setUserRole = useMutation({
    mutationFn: ({ userId, payload }: { userId: number; payload: { is_admin?: boolean; is_active?: boolean } }) =>
      api.setUserRole(userId, payload),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin", "system-status"] });
    },
  });

  const deleteUser = useMutation({
    mutationFn: (userId: number) => api.deleteUser(userId),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin", "system-status"] });
    },
  });

  const createInvite = useMutation({
    mutationFn: () => api.createInvite(),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin", "invites"] });
    },
  });

  const revokeInvite = useMutation({
    mutationFn: (code: string) => api.revokeInvite(code),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin", "invites"] });
    },
  });

  const triggerJob = useMutation({
    mutationFn: (jobName: string) => api.triggerJob(jobName),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["admin", "system-status"] });
      void qc.invalidateQueries({ queryKey: ["admin", "source-runs"] });
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
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
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
            className={activeTab === "sources" ? "btn-primary" : "btn-ghost"}
            onClick={() => setActiveTab("sources")}
            style={{ fontSize: 13, padding: "6px 12px" }}
          >
            Job Sources
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
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {/* Quick Health Summary Grid */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))", gap: 12 }}>
            <div className="card" style={{ background: "var(--bg-raised, #f8fafc)", padding: 12, margin: 0 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontWeight: 600, fontSize: 13 }}>Database</span>
                <span className="chip chip-hit">Connected</span>
              </div>
              <div className="cell-dim" style={{ fontSize: 12, marginTop: 6 }}>
                Backend: <code>{data.database.dialect}</code> ({data.database.vector_backend})
              </div>
            </div>

            <div className="card" style={{ background: "var(--bg-raised, #f8fafc)", padding: 12, margin: 0 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontWeight: 600, fontSize: 13 }}>Scheduler (APS)</span>
                <span className={`chip ${data.scheduler.running ? "chip-hit" : "chip-miss"}`}>
                  {data.scheduler.running ? "Running" : "Stopped"}
                </span>
              </div>
              <div className="cell-dim" style={{ fontSize: 12, marginTop: 6 }}>
                {data.scheduler.jobs.length} registered jobs
              </div>
            </div>

            <div className="card" style={{ background: "var(--bg-raised, #f8fafc)", padding: 12, margin: 0 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <span style={{ fontWeight: 600, fontSize: 13 }}>Email Delivery</span>
                <span className={`chip ${data.email.configured ? "chip-hit" : "chip-miss"}`}>
                  {data.email.configured ? "Ready" : "Not Set"}
                </span>
              </div>
              <div className="cell-dim" style={{ fontSize: 12, marginTop: 6 }}>
                Backend: <code>{data.email.backend}</code>
              </div>
            </div>
          </div>

          {/* LLM Providers telemetry & probe */}
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8, flexWrap: "wrap", gap: 8 }}>
              <h4 style={{ margin: 0, fontSize: 14 }}>
                LLM Providers &amp; Key Status
                <span className="cell-dim" style={{ fontWeight: "normal", fontSize: 12, marginLeft: 8 }}>
                  (Active: <strong>{data.llm.active_provider || "None"}</strong>)
                </span>
              </h4>
              <button
                type="button"
                className="btn-ghost"
                style={{ fontSize: 12, padding: "3px 10px" }}
                onClick={() => setShowKeyForm(!showKeyForm)}
              >
                {showKeyForm ? "Hide Key Form" : "Update API Keys"}
              </button>
            </div>

            {/* Live API Key update form */}
            {showKeyForm && (
              <div className="card" style={{ background: "var(--bg-raised, #f8fafc)", padding: 14, marginBottom: 14 }}>
                <h5 style={{ margin: "0 0 10px", fontSize: 13 }}>Update Process LLM API Keys (Live in-memory)</h5>
                <p className="cell-dim" style={{ fontSize: 12, margin: "0 0 12px" }}>
                  Provide provider keys below to set them immediately in memory without server reboot. Set environment variables on Render to persist permanently.
                </p>
                <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))", gap: 10 }}>
                  {(["xai", "gemini", "openrouter", "groq", "anthropic", "openai"] as const).map((prov) => (
                    <div key={prov} className="field" style={{ margin: 0 }}>
                      <label style={{ fontSize: 12, textTransform: "uppercase" }}>{prov} Key</label>
                      <input
                        type="password"
                        placeholder="Paste API key…"
                        value={llmKeys[prov]}
                        onChange={(e) => setLlmKeys({ ...llmKeys, [prov]: e.target.value })}
                        style={{ fontSize: 12, padding: "5px 8px" }}
                      />
                    </div>
                  ))}
                </div>
                {keyUpdateMsg && (
                  <div className="banner banner-ok" role="status" style={{ marginTop: 10 }}>
                    {keyUpdateMsg}
                  </div>
                )}
                <div style={{ display: "flex", justifyContent: "flex-end", marginTop: 12 }}>
                  <button
                    type="button"
                    className="btn-primary"
                    style={{ fontSize: 12, padding: "6px 14px" }}
                    disabled={updateLlmKeysMut.isPending}
                    onClick={() => updateLlmKeysMut.mutate(llmKeys)}
                  >
                    {updateLlmKeysMut.isPending ? "Updating…" : "Apply Keys"}
                  </button>
                </div>
              </div>
            )}

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
                      padding: "8px 12px",
                      borderRadius: "var(--radius)",
                      border: "1px solid var(--border)",
                      background: p.is_active ? "var(--accent-bg, rgba(99,102,241,0.06))" : "transparent",
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
                Trigger Scoring
              </button>
              <button
                type="button"
                className="btn-ghost"
                onClick={() => triggerJob.mutate("discover_roles")}
                disabled={triggerJob.isPending}
              >
                Trigger Role Discovery
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
        </div>
      )}

      {/* Tab 2: User Metrics & Account Administration */}
      {activeTab === "users" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {/* User Metrics Counters */}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))", gap: 10 }}>
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

          {/* User Accounts Table */}
          <div>
            <h4 style={{ margin: "8px 0 8px", fontSize: 14 }}>Registered Users</h4>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Email</th>
                    <th>Role</th>
                    <th>Status</th>
                    <th>Email Verified</th>
                    <th>Plan</th>
                    <th>Last Login</th>
                    <th>Joined</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {(data.users.user_list ?? []).map((u: AdminUserItem) => (
                    <tr key={u.id}>
                      <td><strong>{u.email}</strong></td>
                      <td>
                        <span className={`chip ${u.is_admin ? "chip-hit" : ""}`}>
                          {u.is_admin ? "Admin" : "User"}
                        </span>
                      </td>
                      <td>
                        <span className={`chip ${u.is_active ? "chip-hit" : "chip-miss"}`}>
                          {u.is_active ? "Active" : "Inactive"}
                        </span>
                      </td>
                      <td>{u.email_verified ? "✅ Verified" : "❌ Unverified"}</td>
                      <td>
                        <span className={`chip ${u.plan === "paid" ? "chip-accent" : ""}`}>
                          {u.plan === "paid" ? "Paid" : "Free"}
                        </span>
                      </td>
                      <td>{relTime(u.last_login_at)}</td>
                      <td>{new Date(u.created_at).toLocaleDateString()}</td>
                      <td>
                        <div style={{ display: "flex", gap: 4 }}>
                          <button
                            type="button"
                            className="btn-ghost"
                            style={{ fontSize: 11, padding: "2px 6px" }}
                            onClick={() => setUserRole.mutate({ userId: u.id, payload: { is_admin: !u.is_admin } })}
                            title="Toggle Admin role"
                          >
                            {u.is_admin ? "Demote" : "Make Admin"}
                          </button>
                          <button
                            type="button"
                            className="btn-ghost"
                            style={{ fontSize: 11, padding: "2px 6px" }}
                            onClick={() => setUserRole.mutate({ userId: u.id, payload: { is_active: !u.is_active } })}
                            title="Toggle active status"
                          >
                            {u.is_active ? "Deactivate" : "Activate"}
                          </button>
                          <button
                            type="button"
                            className="btn-ghost"
                            style={{ fontSize: 11, padding: "2px 6px", color: "var(--bad)" }}
                            onClick={() => {
                              if (confirm(`Delete account for ${u.email}?`)) deleteUser.mutate(u.id);
                            }}
                          >
                            Delete
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Invite Codes Management */}
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
              <h4 style={{ margin: 0, fontSize: 14 }}>Invite Codes</h4>
              <button
                type="button"
                className="btn-ghost"
                style={{ fontSize: 12, padding: "3px 10px" }}
                disabled={createInvite.isPending}
                onClick={() => createInvite.mutate()}
              >
                + Generate Invite Code
              </button>
            </div>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Code</th>
                    <th>Status</th>
                    <th>Created</th>
                    <th>Used At</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {(invitesQuery.data ?? []).map((inv) => (
                    <tr key={inv.id}>
                      <td><code>{inv.code}</code></td>
                      <td>
                        <span className={`chip ${inv.used ? "chip-miss" : "chip-hit"}`}>
                          {inv.used ? "Used" : "Available"}
                        </span>
                      </td>
                      <td>{new Date(inv.created_at).toLocaleDateString()}</td>
                      <td>{inv.used_at ? new Date(inv.used_at).toLocaleDateString() : "—"}</td>
                      <td>
                        {!inv.used && (
                          <button
                            type="button"
                            className="btn-ghost"
                            style={{ fontSize: 11, padding: "2px 6px", color: "var(--bad)" }}
                            onClick={() => revokeInvite.mutate(inv.code)}
                          >
                            Revoke
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                  {!(invitesQuery.data?.length) && (
                    <tr>
                      <td colSpan={5} className="cell-dim" style={{ textAlign: "center", padding: 12 }}>
                        No invite codes generated yet.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Tab 3: Job Sources Health & Recent Runs */}
      {activeTab === "sources" && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {/* Connector Summary Table */}
          <div>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8, flexWrap: "wrap", gap: 8 }}>
              <h4 style={{ margin: 0, fontSize: 14 }}>Configured Board &amp; Aggregator Connectors</h4>
              <div style={{ display: "flex", gap: 6 }}>
                <button
                  type="button"
                  className="btn-ghost"
                  style={{ fontSize: 12, padding: "3px 10px" }}
                  onClick={() => triggerJob.mutate("poll_curated")}
                >
                  Poll Curated
                </button>
                <button
                  type="button"
                  className="btn-ghost"
                  style={{ fontSize: 12, padding: "3px 10px" }}
                  onClick={() => triggerJob.mutate("poll_broad")}
                >
                  Poll Broad (All Boards)
                </button>
              </div>
            </div>
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

          {/* Recent Source Runs */}
          <div>
            <h4 style={{ margin: "8px 0 8px", fontSize: 14 }}>Recent Source Run Log</h4>
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Source</th>
                    <th>Result</th>
                    <th>Found</th>
                    <th>New</th>
                    <th>Dups</th>
                    <th>Duration</th>
                    <th>Timestamp</th>
                  </tr>
                </thead>
                <tbody>
                  {(sourceRunsQuery.data ?? []).map((r) => (
                    <tr key={r.id}>
                      <td><strong>{r.source}</strong></td>
                      <td>
                        <span className={`chip ${r.ok ? "chip-hit" : "chip-miss"}`}>
                          {r.ok ? "OK" : "Failed"}
                        </span>
                        {r.error && (
                          <div className="cell-dim" style={{ fontSize: 11, color: "var(--bad)" }}>
                            {r.error}
                          </div>
                        )}
                      </td>
                      <td>{r.found}</td>
                      <td>{r.new_jobs}</td>
                      <td>{r.duplicates}</td>
                      <td>{r.duration_ms}ms</td>
                      <td>{new Date(r.started_at).toLocaleTimeString()}</td>
                    </tr>
                  ))}
                  {!(sourceRunsQuery.data?.length) && (
                    <tr>
                      <td colSpan={7} className="cell-dim" style={{ textAlign: "center", padding: 12 }}>
                        No runs recorded yet. Click &quot;Poll Broad&quot; to initiate a sweep.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {/* Tab 4: Feedback Inbox */}
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
            <span className="cell-dim" style={{ fontSize: 12 }}>
              Total: {feedbackData?.total ?? 0} ({feedbackData?.new_count ?? 0} new)
            </span>
          </div>

          {feedbackQuery.isPending ? (
            <Loading label="Loading feedback" />
          ) : !feedbackData?.items.length ? (
            <div className="empty" style={{ padding: 32 }}>
              <div className="empty-title">No feedback entries found</div>
              <div className="cell-dim" style={{ fontSize: 13 }}>Inquiries and bug reports submitted by users will appear here.</div>
            </div>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {feedbackData.items.map((fb: FeedbackItem) => (
                <div
                  key={fb.id}
                  className="card"
                  style={{
                    margin: 0,
                    padding: 14,
                    background: fb.status === "new" ? "var(--bg-raised, #f8fafc)" : "transparent",
                    borderLeft: fb.status === "new" ? "4px solid var(--ac-primary, #6366f1)" : undefined,
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
