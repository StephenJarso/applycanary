import { useEffect, useRef, useState, useCallback, KeyboardEvent, ClipboardEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Profile } from "../api";
import { Chips, ErrorBox, Loading } from "../components";
import { useAuth } from "../context/AuthContext";
import {
  getNotificationPermission,
  triggerLocalDeadlineNotification,
} from "../deadlineNotifications";
import {
  isPushSupported,
  subscribeToPush,
  unsubscribeFromPush,
  getExistingSubscription,
} from "../pushSubscription";
import { AdminPanel } from "../AdminPanel";

const CSV_FIELDS = ["skills", "target_titles", "target_locations", "excluded_companies"] as const;

// ---------------------------------------------------------------------------
// TagInput — chip-based replacement for the plain CSV text field.
// The caller owns the string[] array; this component fires onChange whenever
// the array changes.  Tags are added on Enter or comma, removed with the ×
// button or Backspace when the draft input is empty.
// ---------------------------------------------------------------------------
interface TagInputProps {
  id: string;
  tags: string[];
  onChange: (tags: string[]) => void;
  placeholder?: string;
}

function TagInput({ id, tags, onChange, placeholder }: TagInputProps) {
  const [draft, setDraft] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const commit = useCallback(
    (raw: string) => {
      const value = raw.trim();
      if (value && !tags.includes(value)) {
        onChange([...tags, value]);
      }
      setDraft("");
    },
    [tags, onChange],
  );

  const handleKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "Enter" || e.key === ",") {
      e.preventDefault();
      commit(draft);
    } else if (e.key === "Backspace" && draft === "" && tags.length > 0) {
      onChange(tags.slice(0, -1));
    }
  };

  const handlePaste = (e: ClipboardEvent<HTMLInputElement>) => {
    const text = e.clipboardData.getData("text");
    if (text.includes(",")) {
      e.preventDefault();
      const parts = text.split(",").map((s) => s.trim()).filter(Boolean);
      const next = [...tags];
      for (const p of parts) {
        if (!next.includes(p)) next.push(p);
      }
      onChange(next);
      setDraft("");
    }
    // single value without comma: let browser paste normally
  };

  const remove = (tag: string) => onChange(tags.filter((t) => t !== tag));

  return (
    <div
      className="tag-input"
      onClick={() => inputRef.current?.focus()}
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: 6,
        padding: "6px 10px",
        border: "1px solid var(--border)",
        borderRadius: 6,
        background: "var(--surface)",
        cursor: "text",
        minHeight: 38,
        alignItems: "center",
      }}
    >
      {tags.map((tag) => (
        <span
          key={tag}
          className="chip"
          style={{ display: "inline-flex", alignItems: "center", gap: 4 }}
        >
          {tag}
          <button
            type="button"
            aria-label={`Remove ${tag}`}
            onClick={(e) => { e.stopPropagation(); remove(tag); }}
            style={{
              background: "none",
              border: "none",
              cursor: "pointer",
              padding: "0 2px",
              lineHeight: 1,
              fontSize: "0.85em",
              opacity: 0.7,
            }}
          >
            ×
          </button>
        </span>
      ))}
      <input
        ref={inputRef}
        id={id}
        type="text"
        value={draft}
        placeholder={tags.length === 0 ? (placeholder ?? "Type and press Enter or ,") : ""}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={handleKeyDown}
        onPaste={handlePaste}
        style={{
          border: "none",
          outline: "none",
          background: "transparent",
          flexGrow: 1,
          minWidth: 120,
          padding: "2px 0",
        }}
      />
    </div>
  );
}

export default function ProfilePage() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const { data, isPending, error } = useQuery({ queryKey: ["profile"], queryFn: api.profile });
  const [form, setForm] = useState<Partial<Profile>>({});
  // The key input is deliberately not seeded from the profile (the API only
  // returns a masked value), so it needs its own state. Binding it to a
  // constant `value=""` makes typing and pasting appear to do nothing.
  const [llmKey, setLlmKey] = useState("");
  const [githubToken, setGithubToken] = useState("");
  // The app password is write-only: the API stores it encrypted and only
  // returns whether a mailbox is connected, so it needs its own state.
  const [smtpUser, setSmtpUser] = useState("");
  const [smtpPassword, setSmtpPassword] = useState("");
  const [smtpMessage, setSmtpMessage] = useState("");
  const [saveMessage, setSaveMessage] = useState("");
  const [missingFilters, setMissingFilters] = useState<string[]>([]);
  const [notifPermission, setNotifPermission] = useState(getNotificationPermission());
  const [hasPushSub, setHasPushSub] = useState(false);
  const [pushBusy, setPushBusy] = useState(false);
  const [testNotifMsg, setTestNotifMsg] = useState("");
  const [showResumePreview, setShowResumePreview] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isPushSupported()) {
      getExistingSubscription().then((sub) => setHasPushSub(Boolean(sub)));
    }
  }, []);

  // Seed the form once the profile arrives. Keyed on the fetch so a background
  // refetch does not clobber edits in progress.
  useEffect(() => {
    if (data) setForm(data);
  }, [data]);

  useEffect(() => {
    if (data?.smtp_user) setSmtpUser(data.smtp_user);
  }, [data?.smtp_user]);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["profile"] });
    void qc.invalidateQueries({ queryKey: ["status"] });
  };

  // Saved preferences only affect postings scored after the save, so re-score
  // the top of the existing feed automatically. Bounded to 20: a full sweep is
  // a separate, repeatable click and would make saving feel broken.
  const rescoreAfterSave = useMutation({
    mutationFn: () => api.rescore(20),
    onSuccess: (res) => {
      setSaveMessage(res.message);
      void qc.invalidateQueries();
    },
  });

  const save = useMutation({
    mutationFn: () => api.saveProfile(form),
    onSuccess: () => {
      setLlmKey("");
      setForm((f) => {
        const next = { ...f };
        delete (next as { llm_api_key?: string }).llm_api_key;
        return next;
      });
      const missing = [
        !form.location?.trim() && "location",
        !(form.target_locations ?? []).length && "target locations",
        form.years_experience == null && "years of experience",
      ].filter(Boolean) as string[];
      setMissingFilters(missing);
      setSaveMessage("Profile saved. Re-scoring your existing matches…");
      invalidate();
      rescoreAfterSave.mutate();
    },
  });
  const saveEmailSending = useMutation({
    mutationFn: (disconnect: boolean) =>
      api.saveEmailSending({
        smtp_user: disconnect ? "" : smtpUser,
        smtp_app_password: disconnect ? "" : smtpPassword,
        disconnect,
      }),
    onSuccess: (res) => {
      setSmtpPassword("");
      setSmtpMessage(res.message);
      invalidate();
    },
  });
  const upload = useMutation({
    mutationFn: (file: File) => api.uploadResume(file),
    onSuccess: () => {
      invalidate();
      void qc.invalidateQueries({ queryKey: ["ats"] });
    },
  });
  const github = useMutation({
    mutationFn: async () => {
      if (githubToken) await api.saveProfile({ ...form, github_token: githubToken });
      return api.syncGithub();
    },
    onSuccess: () => {
      setGithubToken("");
      invalidate();
    },
  });
  const disconnectGithub = useMutation({
    mutationFn: () => api.saveProfile({ github_disconnect: true }),
    onSuccess: invalidate,
  });
  const discover = useMutation({ mutationFn: api.discover, onSuccess: invalidate });

  const ats = useQuery({
    queryKey: ["ats"],
    queryFn: api.atsReport,
    enabled: Boolean(data?.has_resume),
    retry: false,
  });

  if (isPending) return <Loading label="Loading profile" />;
  if (error) return <ErrorBox error={error} />;

  const set = <K extends keyof Profile>(key: K, value: Profile[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  return (
    <>
      <div className="page-intro">
        <h2 className="page-title">Profile</h2>
        <p className="page-sub">
          <span className="material-symbols-outlined" aria-hidden="true">person</span>
          Your resume, targets and agent preferences
        </p>
      </div>

      {user?.is_admin && <AdminPanel />}

      <div className="card">
        <h3 className="card-title">
          <span className="material-symbols-outlined" aria-hidden="true">description</span>
          Resume / CV
        </h3>
        {data?.has_resume ? (
          <div style={{ marginBottom: 12 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 4, flexWrap: "wrap" }}>
              <span className="material-symbols-outlined" style={{ color: "var(--accent)" }}>article</span>
              <strong>{data.base_resume_filename || "Uploaded resume"}</strong>
              <span className="chip chip-hit">active CV</span>
            </div>
            <p className="cell-dim" style={{ margin: "2px 0 0" }}>
              {data.resume_words} words extracted · {data.skills.length} skills detected
            </p>
          </div>
        ) : (
          <p className="cell-dim">
            No resume uploaded yet. Scoring, ATS check, and tailoring all require your resume.
          </p>
        )}

        <input
          ref={fileRef}
          type="file"
          accept=".pdf,.docx,.txt,.md"
          style={{ display: "none" }}
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) upload.mutate(file);
          }}
        />
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
          {data?.has_resume && (
            <a
              href={api.baseResumeDownloadUrl()}
              target="_blank"
              rel="noopener noreferrer"
              className="btn btn-primary"
              style={{ display: "inline-flex", alignItems: "center", gap: 6 }}
            >
              <span className="material-symbols-outlined" style={{ fontSize: 18 }}>visibility</span>
              View / Download uploaded CV
            </a>
          )}
          <button onClick={() => fileRef.current?.click()} disabled={upload.isPending} className="btn-secondary">
            {upload.isPending && <span className="spinner" />}
            {data?.has_resume ? "Replace resume" : "Upload resume"}
          </button>
          {data?.has_resume && data.base_resume_text && (
            <button
              type="button"
              className="btn-ghost"
              onClick={() => setShowResumePreview(!showResumePreview)}
            >
              {showResumePreview ? "Hide extracted text" : "Preview extracted text"}
            </button>
          )}
        </div>

        {upload.isError && <ErrorBox error={upload.error} />}
        {upload.isSuccess && (
          <div className="banner banner-ok" role="status">{upload.data.message}</div>
        )}

        {showResumePreview && data?.base_resume_text && (
          <div style={{ marginTop: 14 }}>
            <label>Parsed ATS resume text</label>
            <pre
              className="doc"
              style={{
                maxHeight: 320,
                overflowY: "auto",
                whiteSpace: "pre-wrap",
                fontSize: "0.85em",
                background: "var(--surface)",
                padding: 12,
                borderRadius: 6,
                border: "1px solid var(--border)",
              }}
            >
              {data.base_resume_text}
            </pre>
          </div>
        )}

        {data?.skills.length ? (
          <div className="field" style={{ marginTop: 12 }}>
            <label>Detected skills</label>
            <Chips items={data.skills} variant="accent" max={40} />
          </div>
        ) : null}

        {ats.data && (
          <div style={{ marginTop: 14 }}>
            <label>
              ATS structure score{" "}
              <span className={`score ${ats.data.score >= 75 ? "score-strong" : ats.data.score >= 55 ? "score-mid" : "score-weak"}`}>
                {Math.round(ats.data.score)}
              </span>
            </label>
            {ats.data.findings.map((f) => (
              <div
                key={f.rule}
                className={`violation${f.severity === "critical" ? "" : " violation-warn"}`}
              >
                <strong>{f.message}</strong> {f.fix}
              </div>
            ))}
            {ats.data.findings.length === 0 && (
              <div className="banner banner-ok">No structural problems found.</div>
            )}
          </div>
        )}
      </div>

      {user?.is_admin ? (
        <div className="card">
          <h3 className="card-title">
            <span className="material-symbols-outlined" aria-hidden="true">key</span>
            LLM pipeline
          </h3>
          <p className="cell-dim">
            Your administrator account uses the server LLM configured on Render.
            Personal provider keys are not used for admins.
          </p>
        </div>
      ) : (
        <div className="card">
          <h3 className="card-title">
            <span className="material-symbols-outlined" aria-hidden="true">key</span>
            LLM API Key
          </h3>
          <p className="cell-dim">
            Bring your own provider key so scoring and tailoring use your account
            instead of the operator's. Leave blank to use the server default.
          </p>
          <div className="field">
            <label htmlFor="llm-provider">Provider</label>
            <select
              id="llm-provider"
              value={form.llm_provider ?? ""}
              onChange={(e) => set("llm_provider", e.target.value)}
            >
              <option value="">Use server default</option>
              <option value="xai">xAI (Grok)</option>
              <option value="gemini">Google Gemini</option>
              <option value="openrouter">OpenRouter</option>
              <option value="groq">Groq</option>
              <option value="anthropic">Anthropic</option>
            </select>
          </div>
          <div className="field">
            <label htmlFor="llm-key">API Key</label>
            <input
              id="llm-key"
              type="password"
              placeholder={data?.llm_api_key_masked || "sk-..."}
              value={llmKey}
              autoComplete="off"
              spellCheck={false}
              onChange={(e) => {
                const value = e.target.value;
                setLlmKey(value);
                // Only send the key when the user entered one; blank means
                // "keep the stored key" to the backend.
                setForm((f) => {
                  const next = { ...f };
                  if (value) {
                    (next as { llm_api_key?: string }).llm_api_key = value;
                  } else {
                    delete (next as { llm_api_key?: string }).llm_api_key;
                  }
                  return next;
                });
              }}
            />
            {data?.llm_api_key_masked && (
              <p className="cell-dim" style={{ marginTop: 4 }}>
                Current: {data.llm_api_key_masked}
              </p>
            )}
          </div>
        </div>
      )}

      <div className="card">
        <h3 className="card-title">
          <span className="material-symbols-outlined" aria-hidden="true">tune</span>
          Job Preferences
        </h3>
        <div className="field">
          <label htmlFor="work-type">Preferred work type</label>
          <select
            id="work-type"
            value={form.preferred_work_type ?? "any"}
            onChange={(e) => set("preferred_work_type" as any, e.target.value)}
          >
            <option value="any">Any (remote, hybrid, onsite)</option>
            <option value="remote">Remote only</option>
            <option value="hybrid">Hybrid preferred</option>
            <option value="onsite">Onsite preferred</option>
          </select>
        </div>
        <label className="check" style={{ marginBottom: 14 }}>
          <input
            type="checkbox"
            checked={form.remote_only ?? false}
            onChange={(e) => set("remote_only", e.target.checked)}
          />
          Remote roles only (strict filter — overrides location targets)
        </label>
        <label className="check" style={{ marginBottom: 14 }}>
          <input
            type="checkbox"
            checked={form.open_to_relocation ?? false}
            onChange={(e) => set("open_to_relocation" as any, e.target.checked)}
          />
          Open to relocation or visa-sponsored roles worldwide
        </label>
        <p className="cell-dim" style={{ marginTop: -8, marginBottom: 14 }}>
          When checked, onsite roles abroad that offer visa sponsorship or a relocation package are included in your feed.
        </p>
      </div>

      <div className="card">
        <h3 className="card-title">
          GitHub evidence &amp; projects
          {data?.github_connected && <span className="chip chip-hit">connected</span>}
        </h3>
        <p className="cell-dim">
          Connect GitHub so CV tailoring can ground your projects and skills in real code evidence.
          To allow ApplyCanary full view of your projects including private repositories, provide a
          Personal Access Token with the <strong>repo</strong> scope (or a fine-grained token with
          "All repositories" and Read access to Contents &amp; Metadata).
          Tokens are stored encrypted at rest and never shown back.
        </p>
        {!form.github_username && (
          <p className="cell-dim">Add your GitHub username under Details first.</p>
        )}
        <div className="field">
          <label htmlFor="github-token">GitHub access token (supports public &amp; private repos)</label>
          <input
            id="github-token"
            type="password"
            value={githubToken}
            onChange={(e) => setGithubToken(e.target.value)}
            placeholder="github_pat_… or ghp_… (repo read scope)"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        {data?.github_synced_at ? (
          <p className="cell-dim">
            {data.github_repo_count} repositories synced (public &amp; private) · synced {new Date(data.github_synced_at).toLocaleString()}
          </p>
        ) : null}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            onClick={() => github.mutate()}
            disabled={github.isPending || !form.github_username}
            title={!form.github_username ? "Set a GitHub username first" : undefined}
          >
            {github.isPending && <span className="spinner" />} Connect &amp; sync
          </button>
          {data?.github_connected && (
            <button
              onClick={() => disconnectGithub.mutate()}
              disabled={disconnectGithub.isPending}
              className="btn-ghost"
            >
              Disconnect token
            </button>
          )}
        </div>
        {github.isError && <ErrorBox error={github.error} />}
        {github.isSuccess && (
          <div className="banner banner-ok" role="status">{github.data.message}</div>
        )}
        {disconnectGithub.isSuccess && (
          <div className="banner banner-ok" role="status">GitHub token removed.</div>
        )}
      </div>

      <div className="card">
        <h3 className="card-title">Role discovery</h3>
        <p className="cell-dim">
          Actively searches Adzuna + the web for your target titles, skills and GitHub
          evidence, then fetches and scores the matches. Runs every 6 hours
          automatically — use this to kick one off right now.
        </p>
        <button
          onClick={() => discover.mutate()}
          disabled={discover.isPending}
          title={!data?.target_titles.length && !data?.skills.length ? "Add target titles or skills first" : undefined}
        >
          {discover.isPending && <span className="spinner" />} Discover roles for me
        </button>
        {discover.isError && <ErrorBox error={discover.error} />}
        {discover.isSuccess && (
          <div className="banner banner-ok" role="status">{discover.data.message}</div>
        )}
      </div>

      <div className="card">
        <h3 className="card-title">
          Push Notifications &amp; Match Alerts
          {(notifPermission === "granted" || hasPushSub) && <span className="chip chip-hit">active</span>}
        </h3>
        <p className="cell-dim">
          Receive push notifications directly to your phone or desktop even when you are not actively in the app or signed out.
          You will get alerts for new top matches corresponding to your target roles and approaching opportunity deadlines.
        </p>

        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            {hasPushSub ? (
              <>
                <span className="chip chip-hit">Background Device Push Active</span>
                <button
                  type="button"
                  className="btn-ghost"
                  style={{ color: "var(--bad)" }}
                  disabled={pushBusy}
                  onClick={async () => {
                    setPushBusy(true);
                    await unsubscribeFromPush();
                    setHasPushSub(false);
                    setPushBusy(false);
                  }}
                >
                  {pushBusy ? "Disabling…" : "Disable Background Push"}
                </button>
              </>
            ) : (
              <button
                type="button"
                className="btn-primary"
                disabled={pushBusy}
                onClick={async () => {
                  setPushBusy(true);
                  const ok = await subscribeToPush();
                  if (ok) {
                    setHasPushSub(true);
                    setNotifPermission("granted");
                    setTestNotifMsg("Background push alerts enabled for this device!");
                    setTimeout(() => setTestNotifMsg(""), 4000);
                  }
                  setPushBusy(false);
                }}
              >
                {pushBusy ? "Enabling…" : "Enable Push Notifications on this Device"}
              </button>
            )}

            {notifPermission === "granted" && (
              <button
                type="button"
                className="btn-ghost"
                onClick={() => {
                  triggerLocalDeadlineNotification([
                    {
                      title: "Google Summer of Code",
                      org: "Google",
                      kind: "Paid program",
                      deadline: new Date(Date.now() + 3 * 86400000).toISOString(),
                      daysLeft: 3,
                      url: "https://summerofcode.withgoogle.com",
                    },
                  ]);
                  setTestNotifMsg("Test deadline alert dispatched!");
                  setTimeout(() => setTestNotifMsg(""), 4000);
                }}
              >
                Send test alert
              </button>
            )}
          </div>

          {notifPermission === "denied" && (
            <p className="cell-dim" style={{ color: "var(--bad)", margin: 0 }}>
              Notifications are blocked in your browser settings. Please allow notifications for ApplyCanary to receive background alerts.
            </p>
          )}

          {testNotifMsg && <div className="banner banner-ok" role="status" style={{ margin: 0 }}>{testNotifMsg}</div>}
        </div>
      </div>

      <form
        className="card"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <h3 className="card-title">Details</h3>

        <div className="grid2">
          <Text label="Full name" value={form.full_name ?? ""} onChange={(v) => set("full_name", v)} />
          <Text label="Email" value={form.email ?? ""} onChange={(v) => set("email", v)} type="email" />
          <Text label="Phone" value={form.phone ?? ""} onChange={(v) => set("phone", v)} />
          <Text label="Location" value={form.location ?? ""} onChange={(v) => set("location", v)} />
          <Text label="GitHub username" value={form.github_username ?? ""} onChange={(v) => set("github_username", v)} />
          <Text label="LinkedIn URL" value={form.linkedin_url ?? ""} onChange={(v) => set("linkedin_url", v)} />
          <Text label="Portfolio URL" value={form.portfolio_url ?? ""} onChange={(v) => set("portfolio_url", v)} />
          <Text label="Work authorisation" value={form.work_authorization ?? ""} onChange={(v) => set("work_authorization", v)} />

          <div className="field">
            <label htmlFor="years">Years of experience</label>
            <input
              id="years" type="number" min={0} max={60}
              value={form.years_experience ?? ""}
              onChange={(e) => set("years_experience", e.target.value ? Number(e.target.value) : null)}
            />
          </div>
          <div className="field">
            <label htmlFor="salary">Minimum salary</label>
            <input
              id="salary" type="number" min={0} step={1000}
              value={form.min_salary ?? ""}
              onChange={(e) => set("min_salary", e.target.value ? Number(e.target.value) : null)}
            />
          </div>
          <div className="field">
            <label htmlFor="alert">Email alert threshold %</label>
            <input
              id="alert" type="number" min={0} max={100} step={1}
              value={form.alert_min_score ?? ""}
              onChange={(e) =>
                set("alert_min_score", e.target.value ? Math.max(0, Math.min(100, Number(e.target.value))) : 0)
              }
            />
            <p className="cell-dim">Email me when a job scores at/above this. 0 = off.</p>
          </div>
        </div>

        {CSV_FIELDS.map((field) => (
          <div className="field" key={field}>
            <label htmlFor={field}>
              {field.replace(/_/g, " ").replace(/^\w/, (c) => c.toUpperCase())}
              <span className="cell-dim"> (press Enter or comma to add)</span>
            </label>
            <TagInput
              id={field}
              tags={(form[field] ?? []) as string[]}
              onChange={(tags) => set(field, tags)}
              placeholder="Type and press Enter or ,"
            />
          </div>
        ))}

        <button type="submit" className="btn-primary" disabled={save.isPending}>
          {save.isPending && <span className="spinner" />} Save profile
        </button>
        {save.isError && <ErrorBox error={save.error} />}
        {save.isSuccess && (
          <div
            className={`banner ${missingFilters.length ? "banner-warn" : "banner-ok"}`}
            role="status"
          >
            {saveMessage || "Profile saved."}
            {missingFilters.length > 0 && (
              <>
                {" "}Still missing {missingFilters.join(", ")} — those filters stay off until set.
              </>
            )}
          </div>
        )}
        {rescoreAfterSave.isError && (
          <div className="banner banner-warn" role="status">
            Profile saved, but re-scoring failed:{" "}
            {rescoreAfterSave.error instanceof Error
              ? rescoreAfterSave.error.message
              : String(rescoreAfterSave.error)}
          </div>
        )}
      </form>

      <div className="card" style={{ marginTop: 16 }}>
        <h3 className="card-title">Send applications from my inbox</h3>
        <p className="cell-dim" style={{ marginTop: 0 }}>
          Postings that ask for a CV by email are sent from your own mailbox, so replies
          land where you already read them. For Gmail, turn on 2-Step Verification, create
          an App Password (Google Account → Security → App passwords) and paste it here.
          Each application is only sent after you approve it in Discovery.
        </p>
        {data?.email_sending_configured ? (
          <div className="banner banner-ok" role="status">
            Connected as {data.smtp_user}. Email applications can be sent.
          </div>
        ) : (
          <div className="banner banner-warn" role="status">
            No mailbox connected yet — email applications are drafted but not sent.
          </div>
        )}
        <div className="grid2">
          <div className="field">
            <label htmlFor="smtp-user">Sending address</label>
            <input
              id="smtp-user"
              type="email"
              placeholder="you@gmail.com"
              value={smtpUser}
              onChange={(e) => setSmtpUser(e.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="smtp-password">Gmail app password</label>
            <input
              id="smtp-password"
              type="password"
              autoComplete="new-password"
              placeholder={
                data?.email_sending_configured
                  ? "Leave blank to keep the stored password"
                  : "16-character app password"
              }
              value={smtpPassword}
              onChange={(e) => setSmtpPassword(e.target.value)}
            />
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button
            type="button"
            className="btn-primary"
            disabled={saveEmailSending.isPending || !smtpUser.trim()}
            onClick={() => saveEmailSending.mutate(false)}
          >
            {saveEmailSending.isPending && <span className="spinner" />} Save mailbox
          </button>
          {data?.email_sending_configured && (
            <button
              type="button"
              className="btn-ghost"
              disabled={saveEmailSending.isPending}
              onClick={() => saveEmailSending.mutate(true)}
            >
              Disconnect
            </button>
          )}
        </div>
        {saveEmailSending.isError && <ErrorBox error={saveEmailSending.error} />}
        {smtpMessage && <div className="banner banner-ok" role="status">{smtpMessage}</div>}
      </div>

      <div className="card" style={{ marginTop: 16 }}>
        <h3 className="card-title">
          <span className="material-symbols-outlined" aria-hidden="true">send</span>
          Auto-submit preferences
        </h3>
        <p className="cell-dim" style={{ marginTop: 0 }}>
          When enabled, ApplyCanary will automatically submit approved applications
          for jobs that score at or above your threshold. Each application is still
          reviewed by you in Discovery before sending. Requires a connected mailbox
          (see "Send applications from my inbox" above).
        </p>
        <label className="check" style={{ marginBottom: 14 }}>
          <input
            type="checkbox"
            checked={form.enable_auto_submit ?? false}
            onChange={(e) => set("enable_auto_submit", e.target.checked)}
          />
          Enable auto-submit for approved applications
        </label>
        <div className="grid2">
          <div className="field">
            <label htmlFor="auto-submit-min-score">Minimum score for auto-submit</label>
            <input
              id="auto-submit-min-score"
              type="number"
              min={0}
              max={100}
              step={1}
              value={form.auto_submit_min_score ?? 70}
              onChange={(e) =>
                set("auto_submit_min_score", e.target.value ? Math.max(0, Math.min(100, Number(e.target.value))) : 70)
              }
            />
            <p className="cell-dim">Jobs scoring below this will stay in your review queue.</p>
          </div>
          <div className="field">
            <label htmlFor="daily-apply-cap">Daily application cap</label>
            <input
              id="daily-apply-cap"
              type="number"
              min={1}
              max={50}
              step={1}
              value={form.daily_apply_cap ?? 5}
              onChange={(e) =>
                set("daily_apply_cap", e.target.value ? Math.max(1, Math.min(50, Number(e.target.value))) : 5)
              }
            />
            <p className="cell-dim">Maximum applications sent per 24 hours (all sources combined).</p>
          </div>
        </div>
        {form.enable_auto_submit && !data?.email_sending_configured && (
          <div className="banner banner-warn" role="status" style={{ marginTop: 12 }}>
            Auto-submit is enabled but no mailbox is connected. Applications will be
            drafted but not sent until you configure email sending above.
          </div>
        )}
      </div>
    </>
  );
}

function Text({
  label, value, onChange, type = "text",
}: {
  label: string;
  value: string;
  onChange: (v: string) => void;
  type?: string;
}) {
  const id = label.toLowerCase().replace(/\W+/g, "-");
  return (
    <div className="field">
      <label htmlFor={id}>{label}</label>
      <input id={id} type={type} value={value} onChange={(e) => onChange(e.target.value)} />
    </div>
  );
}
