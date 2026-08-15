import { useMutation, useQuery } from "@tanstack/react-query";
import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, type InterviewState } from "../api";
import { ErrorBox, Loading } from "../components";

declare global {
  interface Window {
    webkitSpeechRecognition?: new () => SpeechRecognitionLike;
    SpeechRecognition?: new () => SpeechRecognitionLike;
  }
}

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((e: { resultIndex: number; results: Array<{ isFinal: boolean; 0: { transcript: string } }> }) => void) | null;
  onend: (() => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  start: () => void;
  stop: () => void;
}

/** Raw 16 kHz mono Int16 PCM recorder — the format Transcribe streams expect. */
function createPcmRecorder() {
  let stream: MediaStream | null = null;
  let ctx: AudioContext | null = null;
  let processor: ScriptProcessorNode | null = null;
  let chunks: Int16Array[] = [];
  let startedAt = 0;
  let stopResolve: ((v: { blob: Blob; duration: number }) => void) | null = null;
  const MAX_MS = 90_000;

  function handleAudio(e: AudioProcessingEvent) {
    if (!stopResolve) return;
    const input = e.inputBuffer.getChannelData(0);
    const buf = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) {
      const s = Math.max(-1, Math.min(1, input[i] ?? 0));
      buf[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    chunks.push(buf);
    if (Date.now() - startedAt > MAX_MS) void stop();
  }

  async function start(): Promise<void> {
    chunks = [];
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    ctx = new AudioContext({ sampleRate: 16000 });
    await ctx.resume();
    const source = ctx.createMediaStreamSource(stream);
    processor = ctx.createScriptProcessor(4096, 1, 1);
    processor.onaudioprocess = handleAudio;
    source.connect(processor);
    processor.connect(ctx.destination);
    startedAt = Date.now();
  }

  function stop(): Promise<{ blob: Blob; duration: number }> {
    return new Promise((resolve) => {
      stopResolve = resolve;
      // Give the processor one last tick, then tear down.
      setTimeout(() => {
        const duration = Math.round((Date.now() - startedAt) / 1000);
        try { processor?.disconnect(); } catch { /* noop */ }
        try { void ctx?.close(); } catch { /* noop */ }
        stream?.getTracks().forEach((t) => t.stop());
        const joined = new Int16Array(chunks.reduce((n, c) => n + c.length, 0));
        let offset = 0;
        for (const c of chunks) { joined.set(c, offset); offset += c.length; }
        const blob = new Blob([joined.buffer], { type: "audio/L16; rate=16000" });
        stopResolve = null;
        resolve({ blob, duration });
      }, 120);
    });
  }

  return { start, stop };
}

function base64FromBlob(blob: Blob): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      const result = reader.result as string;
      resolve(result.split(",")[1] ?? "");
    };
    reader.onerror = reject;
    reader.readAsDataURL(blob);
  });
}

export default function InterviewStudio() {
  const { id } = useParams();
  const jobId = Number(id);
  const [state, setState] = useState<InterviewState | null>(null);
  const [mode, setMode] = useState<"speech" | "text">("speech");
  const [phase, setPhase] = useState<"idle" | "recording" | "evaluating" | "speaking">("idle");
  const [draft, setDraft] = useState("");
  const [error, setError] = useState<string>("");
  const [recSeconds, setRecSeconds] = useState(0);
  const [interim, setInterim] = useState("");
  const [ttsBusy, setTtsBusy] = useState(false);
  const recorderRef = useRef<ReturnType<typeof createPcmRecorder> | null>(null);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const timerRef = useRef<number | null>(null);

  const { data: voice, isPending: voicePending } = useQuery({
    queryKey: ["voice"],
    queryFn: api.voice,
  });

  const job = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.job(jobId),
    enabled: Number.isFinite(jobId),
  });

  const start = useMutation({
    mutationFn: () => api.startInterview(jobId, mode),
    onSuccess: (s) => {
      setState(s);
      setError("");
      if (s.current_question) void speak(s.current_question.question);
    },
    onError: (e) => setError(e instanceof Error ? e.message : String(e)),
  });

  const submit = useMutation({
    mutationFn: (payload: { text?: string; audio_b64?: string; duration_seconds?: number }) =>
      api.answerInterview(jobId, state!.session.id, payload),
    onSuccess: (s) => {
      setState(s);
      setPhase("idle");
      setDraft("");
      setInterim("");
      if (s.current_question) void speak(s.current_question.question);
    },
    onError: (e) => {
      setPhase("idle");
      setError(e instanceof Error ? e.message : String(e));
    },
  });

  useEffect(() => () => stopTimers(), []);

  function stopTimers() {
    if (timerRef.current !== null) {
      window.clearInterval(timerRef.current);
      timerRef.current = null;
    }
  }

  async function speak(text: string) {
    if (!voice) return;
    setTtsBusy(true);
    try {
      if (voice.tts === "polly") {
        const { audio_b64, content_type } = await api.tts(text);
        const audio = new Audio(`data:${content_type};base64,${audio_b64}`);
        await audio.play();
      } else {
        const utter = new SpeechSynthesisUtterance(text);
        const preferred = speechSynthesis.getVoices().find((v) =>
          v.lang.startsWith("en") && v.localService);
        if (preferred) utter.voice = preferred;
        speechSynthesis.cancel();
        speechSynthesis.speak(utter);
      }
    } catch {
      setError("Voice synthesis failed — read the question on screen.");
    } finally {
      setTtsBusy(false);
    }
  }

  async function beginRecording() {
    if (!voice || !state) return;
    setError("");
    setInterim("");
    stopTimers();
    setRecSeconds(0);

    if (voice.stt === "browser") {
      const Ctor = window.SpeechRecognition ?? window.webkitSpeechRecognition;
      if (!Ctor) {
        setError("This browser has no speech recognition — type your answer instead.");
        return;
      }
      const rec = new Ctor();
      rec.lang = "en-US";
      rec.continuous = true;
      rec.interimResults = true;
      rec.onresult = (e) => {
        let finalText = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const res = e.results[i];
          if (!res) continue;
          if (res.isFinal) finalText += res[0]?.transcript + " ";
          else setInterim(res[0]?.transcript ?? "");
        }
        if (finalText.trim()) {
          setDraft((prev) => (prev ? `${prev} ${finalText}` : finalText).trim());
        }
      };
      rec.onend = () => { setPhase("idle"); stopTimers(); };
      rec.onerror = (e) => {
        setPhase("idle");
        stopTimers();
        setError(`Speech recognition error: ${e.error}`);
      };
      recognitionRef.current = rec;
      rec.start();
      setPhase("recording");
      timerRef.current = window.setInterval(() => setRecSeconds((s) => s + 1), 1000);
    } else {
      try {
        const recorder = createPcmRecorder();
        recorderRef.current = recorder;
        await recorder.start();
        setPhase("recording");
        timerRef.current = window.setInterval(() => setRecSeconds((s) => s + 1), 1000);
      } catch {
        setError("Microphone access denied — allow the mic or type your answer.");
      }
    }
  }

  async function stopRecording() {
    stopTimers();
    const recorder = recorderRef.current;
    if (voice?.stt === "browser" && recognitionRef.current) {
      recognitionRef.current.stop();
      setPhase("evaluating");
      // The final transcript may arrive on onend; give it a beat.
      await new Promise((r) => setTimeout(r, 700));
      const answer = draft.trim();
      if (answer.length >= 4) {
        submit.mutate({ text: answer, duration_seconds: recSeconds });
      } else {
        setPhase("idle");
        setError("No speech detected — try again or type your answer.");
      }
      return;
    }
    if (recorder) {
      setPhase("evaluating");
      const { blob, duration } = await recorder.stop();
      const audio_b64 = await base64FromBlob(blob);
      submit.mutate({ audio_b64, duration_seconds: duration });
    }
  }

  async function submitTyped() {
    const answer = draft.trim();
    if (answer.length < 4) {
      setError("Type a real answer first.");
      return;
    }
    setPhase("evaluating");
    submit.mutate({ text: answer, duration_seconds: 0 });
  }

  if (voicePending) return <Loading label="Checking voice engines" />;
  if (!Number.isFinite(jobId)) return null;

  const question = state?.current_question;
  const finished = state?.session.finished;
  const turn = state?.turn;

  return (
    <>
      <Link to={`/job/${jobId}`} className="btn-ghost btn-sm" style={{ marginBottom: 14 }}>← Back to job</Link>

      <div className="studio-head">
        <div>
          <h2 className="detail-title">AI Interview Studio</h2>
          <div className="detail-meta">
            {job.data ? (
              <><strong style={{ color: "var(--text)" }}>{job.data.title}</strong>
                <span>·</span><span>{job.data.company}</span></>
            ) : null}
            {voice && (
              <span className="chip chip-accent" title="Which engines power the session">
                {voice.tts === "polly" ? "Polly voice" : "Browser voice"}
                {" · "}
                {voice.stt === "transcribe" ? "Transcribe hearing" : "Browser hearing"}
              </span>
            )}
          </div>
        </div>

        {!state && (
          <div className="seg" role="group" aria-label="Interview mode">
            <button type="button" aria-pressed={mode === "speech"} onClick={() => setMode("speech")}>
              🎙 Voice
            </button>
            <button type="button" aria-pressed={mode === "text"} onClick={() => setMode("text")}>
              ⌨ Typed
            </button>
          </div>
        )}
      </div>

      {error && <div className="banner banner-bad" role="alert">{error}</div>}

      {!state ? (
        <div className="studio-card">
          <p className="prose" style={{ color: "var(--text-dim)", maxWidth: 560 }}>
            A live mock interview for this posting. The coach asks the questions
            an interviewer would, listens to your answers, and scores each one
            against the rubric — drawing on what it remembers about you from
            previous sessions. Answers are transcribed and stored with the
            session, so you can close the tab and resume where you left off.
          </p>
          {voice && (
            <div className="studio-engine-row">
              <EngineBadge on={voice.aws_enabled} label="Amazon Bedrock + Polly + Transcribe" />
              <EngineBadge on={voice.tts === "polly"} label="Neural voice" />
              <EngineBadge on={voice.stt === "transcribe"} label="AWS speech-to-text" />
              <EngineBadge on={!voice.aws_enabled} label="Zero-config browser mode" />
            </div>
          )}
          <button
            className="btn-primary"
            onClick={() => start.mutate()}
            disabled={start.isPending}
            style={{ marginTop: 6 }}
          >
            {start.isPending ? <span className="spinner" /> : null}
            Start interview
          </button>
          {start.isError && <ErrorBox error={start.error} />}
        </div>
      ) : finished ? (
        <SummaryPanel state={state} />
      ) : (
        <>
          <div className="progress-row">
            <div className="progress-track">
              <div
                className="progress-fill"
                style={{ width: `${(state.session.question_index / state.session.total_questions) * 100}%` }}
              />
            </div>
            <span className="num">
              Question {Math.min(state.session.question_index + 1, state.session.total_questions)}
              /{state.session.total_questions}
            </span>
          </div>

          {question && (
            <div className="studio-card">
              <div className="interviewer-row">
                <div className="interviewer-avatar" aria-hidden="true">◆</div>
                <div className="interviewer-bubble">
                  <div className="interviewer-name">Coach</div>
                  <div className="interviewer-question">{question.question}</div>
                  {question.time_minutes ? (
                    <div className="muted" style={{ fontSize: 11, marginTop: 6 }}>
                      ⏱ aim for ~{question.time_minutes} min
                    </div>
                  ) : null}
                </div>
              </div>
              <div className="studio-actions">
                <button onClick={() => speak(question.question)} disabled={ttsBusy} className="btn-sm">
                  {ttsBusy ? <span className="spinner" /> : "🔊"} Replay
                </button>
                {phase === "recording" ? (
                  <button className="btn-danger" onClick={() => void stopRecording()}>
                    ■ Stop &amp; submit
                  </button>
                ) : (
                  <button
                    className="btn-primary"
                    onClick={() => void beginRecording()}
                    disabled={phase === "evaluating" || submit.isPending}
                  >
                    {phase === "evaluating" ? <span className="spinner" /> : "🎙"} Answer aloud
                  </button>
                )}
              </div>

              {phase === "recording" && (
                <div className="recording-row" role="status" aria-live="polite">
                  <span className="rec-dot" /> Recording {recSeconds}s
                  <span className="waveform" aria-hidden="true">
                    {Array.from({ length: 12 }, (_, i) => (
                      <span key={i} style={{ animationDelay: `${(i % 5) * 0.09}s` }} />
                    ))}
                  </span>
                </div>
              )}
              {interim && (
                <p className="muted" style={{ fontStyle: "italic", marginTop: 8 }}>“{interim}…”</p>
              )}

              <textarea
                rows={3}
                placeholder="Or type your answer here — it is scored the same way."
                value={draft}
                onChange={(e) => setDraft(e.target.value)}
                style={{ marginTop: 10 }}
              />
              <div className="studio-actions" style={{ marginTop: 8 }}>
                <button className="btn-sm" onClick={() => void submitTyped()} disabled={phase === "evaluating" || submit.isPending}>
                  Submit typed answer
                </button>
              </div>
            </div>
          )}

          {state.memory.length > 0 && (
            <div className="studio-card memory-card">
              <h3 className="card-title">🧠 Coach remembers</h3>
              {state.memory.map((m, i) => (
                <p key={i} className="prose" style={{ marginBottom: 6 }}>{m.content}</p>
              ))}
            </div>
          )}

          {turn && <TurnFeedback turn={turn} />}
        </>
      )}
    </>
  );
}

function EngineBadge({ on, label }: { on: boolean; label: string }) {
  return (
    <span className={`chip ${on ? "chip-hit" : "chip-miss"}`}>
      {on ? "●" : "○"} {label}
    </span>
  );
}

function TurnFeedback({ turn }: { turn: NonNullable<InterviewState["turn"]> }) {
  return (
    <div className="studio-card">
      <h3 className="card-title">
        Feedback
        <span className={`score ${(turn.score ?? 0) >= 70 ? "score-strong" : (turn.score ?? 0) >= 40 ? "score-mid" : "score-weak"}`}>
          {Math.round(turn.score ?? 0)}
        </span>
        {turn.model_used && <span className="chip">{turn.model_used}</span>}
      </h3>
      <p className="prose" style={{ color: "var(--text)" }}>{turn.feedback}</p>
      <div className="grid2" style={{ marginTop: 8 }}>
        {turn.strengths.length > 0 && (
          <div className="field">
            <label>Strengths</label>
            <ul className="prose" style={{ paddingLeft: 18, margin: 0 }}>
              {turn.strengths.map((s) => <li key={s}>{s}</li>)}
            </ul>
          </div>
        )}
        {turn.improvements.length > 0 && (
          <div className="field">
            <label>Work on</label>
            <ul className="prose" style={{ paddingLeft: 18, margin: 0, color: "var(--warn)" }}>
              {turn.improvements.map((s) => <li key={s}>{s}</li>)}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

function SummaryPanel({ state }: { state: InterviewState }) {
  const summary = state.session.summary ?? {};
  const strengths = (summary.strengths as string[]) ?? [];
  const improvements = (summary.improvements as string[]) ?? [];
  return (
    <div className="studio-card">
      <h3 className="card-title">
        Session complete
        <span className={`score ${(state.session.avg_score ?? 0) >= 70 ? "score-strong" : (state.session.avg_score ?? 0) >= 40 ? "score-mid" : "score-weak"}`}>
          {Math.round(state.session.avg_score ?? 0)}
        </span>
      </h3>
      <p className="prose" style={{ color: "var(--text-dim)", maxWidth: 560 }}>
        {state.session.total_questions} questions answered. This session is now
        part of your agent memory — the coach will recall it (semantically) the
        next time you practise, so you get better with every run.
      </p>
      <div className="grid2" style={{ marginTop: 8 }}>
        {strengths.length > 0 && (
          <div className="field">
            <label>Consistent strengths</label>
            <ul className="prose" style={{ paddingLeft: 18, margin: 0 }}>
              {strengths.map((s) => <li key={s}>{s}</li>)}
            </ul>
          </div>
        )}
        {improvements.length > 0 && (
          <div className="field">
            <label>Focus areas for next time</label>
            <ul className="prose" style={{ paddingLeft: 18, margin: 0, color: "var(--warn)" }}>
              {improvements.map((s) => <li key={s}>{s}</li>)}
            </ul>
          </div>
        )}
      </div>
      <div className="studio-actions" style={{ marginTop: 12 }}>
        <Link to="/memory" className="btn">See your memory</Link>
        <Link to={`/job/${state.session.job_id}`} className="btn-ghost">Back to job</Link>
      </div>
    </div>
  );
}
