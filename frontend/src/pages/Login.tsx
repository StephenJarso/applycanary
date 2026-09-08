import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { ApiError, api } from "../api";
import AuthShell from "./AuthShell";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  // Login's only 403 is an unconfirmed address, so the status alone tells us to
  // offer a resend rather than let the user retype correct credentials.
  const [unverified, setUnverified] = useState(false);
  const [resent, setResent] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setUnverified(false);
    setResent(false);
    setBusy(true);
    try {
      await login(email, password);
      navigate("/", { replace: true });
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) setUnverified(true);
      else setError(err instanceof Error ? err.message : "Invalid credentials");
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

  return (
    <AuthShell title="Welcome back" subtitle="Log in to Agentic Workspace to continue." onSubmit={submit}>
      <button type="button" className="auth-social" disabled>
        <span className="material-symbols-outlined" aria-hidden="true">login</span>
        Continue with Google
      </button>
      <button type="button" className="auth-social" disabled>
        <span className="material-symbols-outlined" aria-hidden="true">code</span>
        Continue with GitHub
      </button>

      <div className="auth-divider">
        <span>OR CONTINUE WITH EMAIL</span>
      </div>

      {unverified && !resent && (
        <div className="banner banner-bad" role="status">
          Confirm your email address before signing in.{" "}
          <button type="button" className="banner-action" onClick={() => void resend()} disabled={busy}>
            Resend confirmation
          </button>
        </div>
      )}
      {unverified && resent && (
        <div className="banner banner-ok" role="status">Confirmation link sent — check your inbox.</div>
      )}
      {error && <div className="banner banner-bad" role="alert">{error}</div>}

      <div className="auth-field">
        <label htmlFor="login-email">Email Address</label>
        <input
          id="login-email" type="text" value={email} onChange={(e) => setEmail(e.target.value)}
          placeholder="name@company.com" autoComplete="username" required autoFocus
        />
      </div>

      <div className="auth-field">
        {/* The link is a sibling of the label rather than inside it: a nested
            anchor makes the label's click target ambiguous. */}
        <div className="auth-label-row">
          <label htmlFor="login-password">Password</label>
          <Link to="/forgot-password">Forgot password?</Link>
        </div>
        <input
          id="login-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
          autoComplete="current-password" required
        />
      </div>

      <button type="submit" disabled={busy}>{busy ? "Signing in…" : "Log In"}</button>

      <p className="auth-foot">
        Don't have an account? <Link to="/register">Sign up</Link> · <Link to="/guest">Browse as guest</Link>
      </p>
    </AuthShell>
  );
}
