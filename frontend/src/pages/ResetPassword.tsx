import { useState, type FormEvent } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { api } from "../api";
import AuthShell from "./AuthShell";

export default function ResetPassword() {
  const [params] = useSearchParams();
  const token = params.get("token") ?? "";
  const navigate = useNavigate();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (password !== confirm) {
      setError("Those passwords don't match");
      return;
    }
    setError("");
    setBusy(true);
    try {
      await api.auth.resetPassword(token, password);
      setDone(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reset your password");
    } finally {
      setBusy(false);
    }
  }

  if (!token) {
    return (
      <AuthShell
        title="Link incomplete"
        subtitle="This reset link is missing its token. Request a new one and open the link directly from the email."
        glyph="!" tone="bad"
      >
        <Link className="auth-submit" to="/forgot-password">Request a new link</Link>
      </AuthShell>
    );
  }

  if (done) {
    return (
      <AuthShell
        title="Password updated"
        subtitle="You're all set. Sign in with your new password — any other devices have been signed out."
        glyph="✓"
      >
        <button type="button" className="auth-submit" onClick={() => navigate("/login", { replace: true })}>
          Go to sign in
        </button>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Set a new password" subtitle="Choose a password of at least 10 characters." onSubmit={submit}>
      {error && <div className="banner banner-bad" role="alert">{error}</div>}

      <div className="auth-field">
        <label htmlFor="new-password">New Password</label>
        <input
          id="new-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
          autoComplete="new-password" minLength={10} required autoFocus
        />
      </div>

      <div className="auth-field">
        <label htmlFor="confirm-password">Confirm Password</label>
        <input
          id="confirm-password" type="password" value={confirm} onChange={(e) => setConfirm(e.target.value)}
          autoComplete="new-password" minLength={10} required
        />
      </div>

      <button type="submit" disabled={busy}>{busy ? "Saving…" : "Update password"}</button>

      <p className="auth-foot"><Link to="/login">Back to sign in</Link></p>
    </AuthShell>
  );
}
