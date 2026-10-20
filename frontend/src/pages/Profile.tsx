import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Profile } from "../api";
import { Chips, ErrorBox, Loading } from "../components";
import { useAuth } from "../context/AuthContext";
import {
  getNotificationPermission,
  requestNotificationPermission,
  triggerLocalDeadlineNotification,
} from "../deadlineNotifications";
import { AdminPanel } from "../AdminPanel";

const CSV_FIELDS = ["skills", "target_titles", "target_locations", "excluded_companies"] as const;

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
  const [notifPermission, setNotifPermission] = useState(getNotificationPermission());
  const [testNotifMsg, setTestNotifMsg] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  // Seed the form once the profile arrives. Keyed on the fetch so a background
  // refetch does not clobber edits in progress.
  useEffect(() => {
    if (data) setForm(data);
  }, [data]);

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ["profile"] });
    void qc.invalidateQueries({ queryKey: ["status"] });
  };

  const save = useMutation({
    mutationFn: () => api.saveProfile(form),
    onSuccess: () => {
      setLlmKey("");
      setForm((f) => {
        const next = { ...f };
        delete (next as { llm_api_key?: string }).llm_api_key;
        return next;
      });
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
        <h3 className="card-title">Resume</h3>
        {data?.has_resume ? (
          <p className="cell-dim">
            {data.resume_words} words · {data.skills.length} skills detected
          </p>
        ) : (
          <p className="cell-dim">
            No resume yet. Scoring and tailoring both need one.
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
        <button onClick={() => fileRef.current?.click()} disabled={upload.isPending}>
          {upload.isPending && <span className="spinner" />}
          {data?.has_resume ? "Replace resume" : "Upload resume"}
        </button>

        {upload.isError && <ErrorBox error={upload.error} />}
        {upload.isSuccess && (
          <div className="banner banner-ok" role="status">{upload.data.message}</div>
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
          Remote roles only (strict filter)
        </label>
      </div>

      <div className="card">
        <h3 className="card-title">
          GitHub evidence
          {data?.github_connected && <span className="chip chip-hit">connected</span>}
        </h3>
        <p className="cell-dim">
          Connect GitHub so CV tailoring can use real repositories as evidence.
          A fine-grained token with read-only access to public repositories is
          enough. It is stored encrypted and never shown back.
        </p>
        {!form.github_username && (
          <p className="cell-dim">Add your GitHub username under Details first.</p>
        )}
        <div className="field">
          <label htmlFor="github-token">GitHub access token (optional)</label>
          <input
            id="github-token"
            type="password"
            value={githubToken}
            onChange={(e) => setGithubToken(e.target.value)}
            placeholder="github_pat_… (read-only)"
            autoComplete="off"
            spellCheck={false}
          />
        </div>
        {data?.github_synced_at ? (
          <p className="cell-dim">
            {data.github_repo_count} repos · synced {new Date(data.github_synced_at).toLocaleString()}
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
          Deadline notifications
          {notifPermission === "granted" && <span className="chip chip-hit">active</span>}
        </h3>
        <p className="cell-dim">
          Get daily local notifications on this device when application deadlines for
          internships, paid programs, or tech events are approaching (closing within 7 days).
        </p>
        {notifPermission === "granted" ? (
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <span className="chip chip-hit">Device alerts enabled</span>
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
                setTestNotifMsg("Test deadline notification dispatched!");
                setTimeout(() => setTestNotifMsg(""), 4000);
              }}
            >
              Send test notification
            </button>
          </div>
        ) : notifPermission === "denied" ? (
          <p className="cell-dim" style={{ color: "var(--bad)" }}>
            Notifications are blocked in your browser permissions. Enable notifications for this site to receive daily deadline alerts.
          </p>
        ) : (
          <button
            type="button"
            onClick={async () => {
              const res = await requestNotificationPermission();
              setNotifPermission(res);
            }}
          >
            Enable daily deadline notifications
          </button>
        )}
        {testNotifMsg && <div className="banner banner-ok" role="status" style={{ marginTop: 8 }}>{testNotifMsg}</div>}
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
              <span className="cell-dim"> (comma separated)</span>
            </label>
            <input
              id={field}
              type="text"
              value={(form[field] ?? []).join(", ")}
              onChange={(e) =>
                set(field, e.target.value.split(",").map((s) => s.trim()).filter(Boolean))
              }
            />
          </div>
        ))}

        <button type="submit" className="btn-primary" disabled={save.isPending}>
          {save.isPending && <span className="spinner" />} Save profile
        </button>
        {save.isError && <ErrorBox error={save.error} />}
        {save.isSuccess && <div className="banner banner-ok" role="status">Profile saved.</div>}
      </form>
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
