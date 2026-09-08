import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import AuthShell from "./AuthShell";

export default function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await api.auth.forgotPassword(email);
      setSent(true);
    } catch (err) {
      // The endpoint is a deliberate 204 for unknown addresses, so anything
      // thrown here is a real fault (rate limit, network, mail misconfigured).
      setError(err instanceof Error ? err.message : "Could not send the reset link");
    } finally {
      setBusy(false);
    }
  }

  if (sent) {
    return (
      <AuthShell
        title="Check your inbox"
        subtitle={<>If an account exists for <strong>{email}</strong>, a reset link is on its way. It expires in one hour.</>}
        glyph="✉"
      >
        <p className="auth-foot"><Link to="/login">Back to sign in</Link></p>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Reset your password" subtitle="Enter your email and we'll send you a link to set a new password." onSubmit={submit}>
      {error && <div className="banner banner-bad" role="alert">{error}</div>}

      <div className="auth-field">
        <label htmlFor="forgot-email">Email Address</label>
        <input
          id="forgot-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
          placeholder="name@company.com" autoComplete="username" required autoFocus
        />
      </div>

      <button type="submit" disabled={busy}>{busy ? "Sending…" : "Send reset link"}</button>

      <p className="auth-foot">
        Remembered it? <Link to="/login">Back to sign in</Link>
      </p>
    </AuthShell>
  );
}
