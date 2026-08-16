import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Profile } from "../api";
import { Chips, ErrorBox, Loading } from "../components";

const CSV_FIELDS = ["skills", "target_titles", "target_locations", "excluded_companies"] as const;

export default function ProfilePage() {
  const qc = useQueryClient();
  const { data, isPending, error } = useQuery({ queryKey: ["profile"], queryFn: api.profile });
  const [form, setForm] = useState<Partial<Profile>>({});
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

  const save = useMutation({ mutationFn: () => api.saveProfile(form), onSuccess: invalidate });
  const upload = useMutation({
    mutationFn: (file: File) => api.uploadResume(file),
    onSuccess: () => {
      invalidate();
      void qc.invalidateQueries({ queryKey: ["ats"] });
    },
  });
  const github = useMutation({ mutationFn: api.syncGithub, onSuccess: invalidate });
  const discover = useMutation({ mutationFn: api.discover, onSuccess: invalidate });
  const invite = useQuery({ queryKey: ["invite"], queryFn: api.auth.invite, retry: false });

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
      <div className="card">
        <h3 className="card-title">Invite someone</h3>
        <p className="cell-dim">Share this single-use link to invite someone to create an account.</p>
        {invite.isPending && <Loading label="Loading invite code" />}
        {invite.isError && <ErrorBox error={invite.error} />}
        {invite.data && (
          <div className="field" style={{ marginTop: 12 }}>
            <label htmlFor="invite-link">Your invite link</label>
            <input id="invite-link" readOnly value={`${window.location.origin}${invite.data.link}`} onFocus={(e) => e.currentTarget.select()} />
            <p className="cell-dim">Code: <code>{invite.data.code}</code> · Click the field to copy.</p>
          </div>
        )}
      </div>
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

      <div className="card">
        <h3 className="card-title">GitHub evidence</h3>
        <p className="cell-dim">
          Public repos are scanned for verifiable proof of skills, so CV tailoring can
          surface real work instead of inventing it.
        </p>
        {data?.github_synced_at ? (
          <p className="cell-dim">
            {data.github_repo_count} repos · synced {new Date(data.github_synced_at).toLocaleString()}
          </p>
        ) : null}
        <button
          onClick={() => github.mutate()}
          disabled={github.isPending || !form.github_username}
          title={!form.github_username ? "Set a GitHub username first" : undefined}
        >
          {github.isPending && <span className="spinner" />} Sync GitHub
        </button>
        {github.isError && <ErrorBox error={github.error} />}
        {github.isSuccess && (
          <div className="banner banner-ok" role="status">{github.data.message}</div>
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

        <label className="check" style={{ marginBottom: 14 }}>
          <input
            type="checkbox"
            checked={form.remote_only ?? false}
            onChange={(e) => set("remote_only", e.target.checked)}
          />
          Remote roles only
        </label>

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
