import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { ApiError, api } from "../api";

/**
 * Inline auth panels embedded in the Landing page.
 *
 * The landing page swaps its hero for one of two panels when the visitor
 * clicks Login / Sign-Up in its own nav — marketing and auth share one route
 * ("/"), and signing out returns exactly here (App.tsx keeps /login and
 * /register as redirects for old links). The forms are the same logic as the
 * former Login.tsx / Register.tsx pages, minus the standalone AuthShell
 * chrome: they live inside the landing layout now.
 */

export type AuthMode = "login" | "register";

interface Props {
  mode: AuthMode;
  /** Flip between login and register without leaving the page. */
  onModeChange: (mode: AuthMode) => void;
}

export default function AuthPanel({ mode, onModeChange }: Props) {
  const { login, register } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  // Login's only 403 is an unconfirmed address; register may create the
  // account without a session when email verification is enforced.
  const [unverified, setUnverified] = useState(false);
  const [awaitingConfirmation, setAwaitingConfirmation] = useState(false);
  const [resent, setResent] = useState(false);
  const [busy, setBusy] = useState(false);

  const showValidation = (event: FormEvent<HTMLInputElement>) => {
    setError(event.currentTarget.validationMessage || "Please check the highlighted fields.");
  };

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setUnverified(false);
    setResent(false);
    setBusy(true);
    try {
      if (mode === "login") {
        await login(email, password);
        // Full app navigation is App.tsx's job; the landing panel simply
        // reports success by clearing all marketing/panel state upstream.
        window.location.assign("/jobs");
        return; // avoid setState-after-unmount as the page navigates
      }
      const created = await register(email, password);
      if (created.session_started) {
        window.location.assign("/profile");
        return;
      }
      setAwaitingConfirmation(true);
    } catch (err) {
      if (mode === "login" && err instanceof ApiError && err.status === 403) {
        setUnverified(true);
      } else {
        setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  async function resend() {
    setBusy(true);
    try {
      await api.auth.resendVerification(email, password);
      setResent(true);
    } catch {
      /* Deliberately 204 for unknown accounts; nothing useful to report. */
      setResent(true);
    } finally {
      setBusy(false);
    }
  }

  // Outcome screens replace the form so a registrant always sees what to do
  // next without hunting — identical copy and behaviour to the old pages.
  if (awaitingConfirmation) {
    return (
      <div className="auth-panel">
        <div className="auth-mark" aria-hidden="true">✉</div>
        <h1 className="auth-panel-title">Check your inbox</h1>
        <p className="auth-panel-sub">
          We sent a confirmation link to <strong>{email}</strong>. Open it to activate your account.
        </p>
        <button
          type="button"
          className="btn-ghost auth-panel-switch"
          onClick={() => { setAwaitingConfirmation(false); onModeChange("login"); }}
        >
          Use a different address
        </button>
      </div>
    );
  }

  return (
    <form className="auth-panel auth-inline" onSubmit={submit}>
      <div className="auth-mark" aria-hidden="true">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <rect x="2" y="7" width="20" height="14" rx="2" />
          <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16" />
        </svg>
      </div>
      <h1 className="auth-panel-title">{mode === "login" ? "Welcome back" : "Create your account"}</h1>
      <p className="auth-panel-sub">
        {mode === "login"
          ? "Sign in to continue to your dashboard."
          : "Add your email and a password to get started."}
      </p>

      {/* Email-verification states, re-laid for the inline panel */}
      {mode === "login" && unverified && !resent && (
        <div className="banner banner-bad" role="status">
          Confirm your email address before signing in.{" "}
          <button type="button" className="banner-action" onClick={() => void resend()} disabled={busy}>
            Resend confirmation
          </button>
        </div>
      )}
      {mode === "login" && unverified && resent && (
        <div className="banner banner-ok" role="status">Confirmation link sent — check your inbox.</div>
      )}
      {error && <div className="banner banner-bad" role="alert">{error}</div>}

      <div className="auth-field">
        <label htmlFor="panel-email">Email Address</label>
        <input
          id="panel-email" type={mode === "login" ? "text" : "email"} value={email}
          onChange={(e) => setEmail(e.target.value)}
          placeholder="name@company.com" autoComplete="username" required autoFocus
          onInvalid={showValidation}
        />
      </div>

      <div className="auth-field">
        <div className="auth-label-row">
          <label htmlFor="panel-password">Password</label>
          {mode === "login" && (
            <Link to="/forgot-password" tabIndex={3}>
              Forgot password?
            </Link>
          )}
        </div>
        <input
          id="panel-password" type="password" value={password}
          onChange={(e) => setPassword(e.target.value)}
          placeholder={mode === "login" ? "Your password" : "At least 10 characters"}
          autoComplete={mode === "login" ? "current-password" : "new-password"}
          minLength={10} required
          onInvalid={showValidation}
        />
      </div>

      <button type="submit" className="btn-primary auth-panel-submit" disabled={busy}>
        {busy ? (mode === "login" ? "Signing in…" : "Creating…") : (mode === "login" ? "Log In" : "Create account")}
      </button>

      <button
        type="button"
        className="auth-panel-switch"
        onClick={() => { setError(""); setUnverified(false); onModeChange(mode === "login" ? "register" : "login"); }}
      >
        {mode === "login"
          ? <>Don&apos;t have an account? <strong>Sign up</strong></>
          : <>Already registered? <strong>Sign in</strong></>}
      </button>
    </form>
  );
}
