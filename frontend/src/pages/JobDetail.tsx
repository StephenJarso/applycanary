import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";
import { Chips, ErrorBox, Loading, ScoreBadge, formatSalary, relTime } from "../components";

export default function JobDetail() {
  const { id } = useParams();
  const jobId = Number(id);
  const qc = useQueryClient();

  const { data: job, isPending, error } = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.job(jobId),
    enabled: Number.isFinite(jobId),
  });

  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["job", jobId] });
    void qc.invalidateQueries({ queryKey: ["jobs"] });
    void qc.invalidateQueries({ queryKey: ["status"] });
  };

  const tailor = useMutation({ mutationFn: () => api.tailor(jobId), onSuccess: refresh });
  const submit = useMutation({ mutationFn: () => api.submit(jobId), onSuccess: refresh });
  const skip = useMutation({ mutationFn: () => api.skip(jobId), onSuccess: refresh });
  const prep = useMutation({ mutationFn: () => api.prep(jobId), onSuccess: refresh });

  if (isPending) return <Loading label="Loading job" />;
  if (error) return <ErrorBox error={error} />;
  if (!job) return null;

  const salary = formatSalary(
    job.salary_min, job.salary_max, job.salary_currency, job.salary_is_estimate,
  );
  const version = job.resume_version;
  const busy = tailor.isPending || submit.isPending || skip.isPending || prep.isPending;
  const blocked = version?.truthcheck_passed === false;

  return (
    <>
      <Link to="/" className="btn-ghost btn-sm" style={{ marginBottom: 14 }}>← Jobs</Link>

      <div className="detail-head">
        <ScoreBadge score={job.score} />
        <div>
          <h2 className="detail-title">{job.title}</h2>
          <div className="detail-meta">
            <strong style={{ color: "var(--text)" }}>{job.company}</strong>
            <span>·</span>
            <span>{job.is_remote ? "Remote" : job.location || "Location not stated"}</span>
            {salary && <><span>·</span><span>{salary}</span></>}
            <span>·</span>
            <span>{relTime(job.posted_at ?? job.first_seen_at)} old</span>
            <span>·</span>
            <span className="chip">{job.source}</span>
            <span className="chip">{job.status}</span>
            {job.seen_count > 1 && (
              <span className="chip" title="Deduplicated sightings across boards">
                seen {job.seen_count}×
              </span>
            )}
          </div>
        </div>

        <div className="detail-actions">
          {job.apply_url && (
            <a href={job.apply_url} target="_blank" rel="noopener noreferrer" className="btn btn-sm">
              Open posting ↗
            </a>
          )}
          <button onClick={() => tailor.mutate()} disabled={busy} className="btn-sm">
            {tailor.isPending && <span className="spinner" />} Tailor CV
          </button>
          <button
            onClick={() => submit.mutate()}
            disabled={busy || blocked}
            className="btn-primary btn-sm"
            title={blocked ? "Blocked: tailored CV failed verification" : undefined}
          >
            {submit.isPending && <span className="spinner" />} Submit
          </button>
          <button onClick={() => prep.mutate()} disabled={busy} className="btn-sm">
            Interview prep
          </button>
          <button onClick={() => skip.mutate()} disabled={busy} className="btn-danger btn-sm">
            Skip
          </button>
        </div>
      </div>

      {[tailor, submit, skip, prep].map((m, i) =>
        m.isError ? <ErrorBox key={i} error={m.error} /> : null,
      )}
      {[tailor, submit, prep].map((m, i) =>
        m.isSuccess ? (
          <div key={i} className={`banner banner-${m.data.ok ? "ok" : "warn"}`} role="status">
            {m.data.message}
          </div>
        ) : null,
      )}

      {job.score && (
        <div className="card">
          <h3 className="card-title">
            Match <span className="chip">{job.score.verdict || "unscored"}</span>
            {job.score.decided_by && <span className="chip">{job.score.decided_by}</span>}
          </h3>

          <div className="grid2" style={{ marginBottom: 12 }}>
            <Meter label="Keyword" value={job.score.keyword_score} />
            <Meter label="Semantic" value={job.score.semantic_score} />
            <Meter label="ATS structure" value={job.score.ats_score} />
            <Meter label="Total" value={job.score.total} />
          </div>

          {job.score.disqualifier && (
            <div className="violation">Disqualified: {job.score.disqualifier}</div>
          )}
          {job.score.reasoning && <p className="prose">{job.score.reasoning}</p>}

          {job.score.matched_keywords.length > 0 && (
            <div className="field">
              <label>Matched</label>
              <Chips items={job.score.matched_keywords} variant="hit" max={30} />
            </div>
          )}
          {job.score.missing_keywords.length > 0 && (
            <div className="field">
              <label>Missing from your resume</label>
              <Chips items={job.score.missing_keywords} variant="miss" max={30} />
            </div>
          )}
        </div>
      )}

      {version && (
        <div className="card">
          <h3 className="card-title">
            Tailored CV
            <span className={`chip ${version.truthcheck_passed ? "chip-hit" : "chip-miss"}`}>
              {version.truthcheck_passed ? "verification passed" : "blocked"}
            </span>
          </h3>

          <div className="detail-meta" style={{ marginBottom: 10 }}>
            ATS {version.ats_score_before} → <strong>{version.ats_score_after}</strong>
          </div>

          <div className="side-by-side" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 20, marginTop: 16 }}>
            <div className="panel">
              <h4 style={{ margin: "0 0 8px" }}>Tailored Resume</h4>
              <div className="cv-rendered" style={{ maxHeight: 500, overflow: "auto" }} dangerouslySetInnerHTML={{ __html: version.text_html || version.text }} />
            </div>
            <div className="panel">
              <h4 style={{ margin: "0 0 8px" }}>Job Description</h4>
              <pre className="doc" style={{ maxHeight: 500, overflow: "auto", whiteSpace: "pre-wrap" }}>{job.description || "No description captured."}</pre>
            </div>
          </div>

          {version.truthcheck_notes.map((note) => (
            <div key={note} className="violation">{note}</div>
          ))}
          {version.unverifiable_claims.map((claim) => (
            <div key={claim} className="violation violation-warn">
              Unverifiable: {claim}
            </div>
          ))}

          {version.keywords_added.length > 0 && (
            <div className="field">
              <label>Keywords added</label>
              <Chips items={version.keywords_added} variant="accent" max={30} />
            </div>
          )}

          {version.diff_summary && (
            <details>
              <summary>What changed</summary>
              <p className="prose">{version.diff_summary}</p>
            </details>
          )}
          {version.docx_path && (
            <p className="muted" style={{ marginTop: 8 }}>
              <a href={`/download/${version.id}/docx`}>Download DOCX</a>
              {version.pdf_path && <span> · </span>}
              {version.pdf_path && <a href={`/download/${version.id}/pdf`}>Download PDF</a>}
            </p>
          )}
        </div>
      )}

      {job.application && (
        <div className="card">
          <h3 className="card-title">Application</h3>
          <dl className="kv">
            <dt>Method</dt><dd>{job.application.method}</dd>
            <dt>Queued</dt><dd>{relTime(job.application.queued_at)} ago</dd>
            <dt>Submitted</dt>
            <dd>{job.application.submitted_at ? `${relTime(job.application.submitted_at)} ago` : "not yet"}</dd>
            {job.application.confirmation && (
              <><dt>Confirmation</dt><dd>{job.application.confirmation}</dd></>
            )}
            {job.application.error && (
              <><dt>Error</dt><dd style={{ color: "var(--bad)" }}>{job.application.error}</dd></>
            )}
            <dt>Attempts</dt><dd className="num">{job.application.attempts}</dd>
          </dl>

          {job.application.cover_letter && (
            <details style={{ marginTop: 10 }}>
              <summary>Cover letter</summary>
              <pre className="doc">{job.application.cover_letter}</pre>
            </details>
          )}
          {Object.keys(job.application.form_answers).length > 0 && (
            <details>
              <summary>Prepared form answers</summary>
              <dl className="kv" style={{ marginTop: 8 }}>
                {Object.entries(job.application.form_answers).map(([k, v]) => (
                  <div key={k} style={{ display: "contents" }}>
                    <dt>{k}</dt><dd>{v}</dd>
                  </div>
                ))}
              </dl>
            </details>
          )}
        </div>
      )}

      {job.interview_prep && (
        <div className="card">
          <h3 className="card-title">Interview prep</h3>
          {job.interview_prep.company_notes && (
            <p className="prose">{job.interview_prep.company_notes}</p>
          )}
          {job.interview_prep.skill_gaps.length > 0 && (
            <div className="field">
              <label>Gaps to prepare for</label>
              <Chips items={job.interview_prep.skill_gaps} variant="miss" max={20} />
            </div>
          )}

          {job.interview_prep.speech_interview && job.interview_prep.speech_interview.length > 0 && (
            <div className="interview-section speech-interview">
              <h3>Speech Interview (30 min simulation)</h3>
              {job.interview_prep.speech_interview.map((sim, idx) => (
                <details key={idx} open>
                  <summary>{sim.question}</summary>
                  <div className="interview-content">
                    {sim.expected_key_points && sim.expected_key_points.length > 0 && (
                      <>
                        <p><strong>Key points to cover:</strong></p>
                        <ul>{sim.expected_key_points.map((pt, i) => <li key={i}>{pt}</li>)}</ul>
                      </>
                    )}
                    {sim.time_minutes && <p className="muted">⏱ {sim.time_minutes} min</p>}
                    {sim.evaluation_rubric && (
                      <details>
                        <summary>Evaluation rubric</summary>
                        <div className="rubric">
                          {Object.entries(sim.evaluation_rubric).map(([level, desc]) => (
                            <p key={level}><strong>{level.charAt(0).toUpperCase() + level.slice(1)}:</strong> {desc}</p>
                          ))}
                        </div>
                      </details>
                    )}
                  </div>
                </details>
              ))}
            </div>
          )}

          {job.interview_prep.technical_interview && job.interview_prep.technical_interview.length > 0 && (
            <div className="interview-section technical-interview">
              <h3>Technical Interview (coding/design)</h3>
              {job.interview_prep.technical_interview.map((sim, idx) => (
                <details key={idx} open>
                  <summary>{sim.question}</summary>
                  <div className="interview-content">
                    {sim.starter_code && (
                      <>
                        <p><strong>Starter code:</strong></p>
                        <pre className="doc">{sim.starter_code}</pre>
                      </>
                    )}
                    {sim.expected_solution && (
                      <details>
                        <summary>Expected solution outline</summary>
                        <pre className="doc">{sim.expected_solution}</pre>
                      </details>
                    )}
                    {sim.time_minutes && <p className="muted">⏱ {sim.time_minutes} min</p>}
                    {sim.evaluation_rubric && (
                      <details>
                        <summary>Evaluation rubric</summary>
                        <div className="rubric">
                          {Object.entries(sim.evaluation_rubric).map(([level, desc]) => (
                            <p key={level}><strong>{level.charAt(0).toUpperCase() + level.slice(1)}:</strong> {desc}</p>
                          ))}
                        </div>
                      </details>
                    )}
                  </div>
                </details>
              ))}
            </div>
          )}

          {[
            ["Technical", job.interview_prep.technical_questions],
            ["Behavioural", job.interview_prep.behavioural_questions],
          ].map(([label, questions]) => {
            const list = questions as Array<{ question: string; answer: string; why: string }>;
            return list.length ? (
              <details key={label as string}>
                <summary>{label as string} questions ({list.length})</summary>
                {list.map((q) => (
                  <div key={q.question} style={{ marginBottom: 12 }}>
                    <div style={{ fontWeight: 540 }}>{q.question}</div>
                    <p className="prose">{q.answer}</p>
                    {q.why && <p className="prose" style={{ fontStyle: "italic" }}>{q.why}</p>}
                  </div>
                ))}
              </details>
            ) : null;
          })}
          {job.interview_prep.questions_to_ask.length > 0 && (
            <details>
              <summary>Questions to ask them</summary>
              <ul className="prose">
                {job.interview_prep.questions_to_ask.map((q) => <li key={q}>{q}</li>)}
              </ul>
            </details>
          )}
        </div>
      )}

      <div className="card">
        <h3 className="card-title">Posting</h3>
        {job.aliases.length > 0 && (
          <div className="field">
            <label>Also seen on</label>
            <div className="chips">
              {job.aliases.map((a) => (
                <span key={`${a.source}-${a.apply_url}`} className="chip" title={a.matched_by}>
                  {a.source}
                  {a.match_score !== null && ` ${Math.round(a.match_score)}%`}
                </span>
              ))}
            </div>
          </div>
        )}
        <p className="prose">{job.description || "No description captured."}</p>
      </div>
    </>
  );
}

function Meter({ label, value }: { label: string; value: number }) {
  return (
    <div className="field">
      <label>{label} <span className="num">{Math.round(value)}</span></label>
      <div className="bar">
        <div
          className="bar-fill"
          style={{ width: `${Math.max(0, Math.min(100, value))}%` }}
          role="meter"
          aria-valuenow={Math.round(value)}
          aria-valuemin={0}
          aria-valuemax={100}
          aria-label={label}
        />
      </div>
    </div>
  );
}
