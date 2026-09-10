import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import AuthShell from "./AuthShell";

export default function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Set when the account was created but no session was issued, because the
  // deployment requires the address to be confirmed first.
  const [awaitingConfirmation, setAwaitingConfirmation] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const created = await register(email, password);
      if (created.session_started) navigate("/profile", { replace: true });
      else setAwaitingConfirmation(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setBusy(false);
    }
  }

  if (awaitingConfirmation) {
    return (
      <AuthShell title="Check your inbox" subtitle={<>We sent a confirmation link to <strong>{email}</strong>. Open it to activate your account.</>} glyph="✉">
        <p className="auth-foot">
          Wrong address or no email? <Link to="/login">Back to sign in</Link>
        </p>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="Create your account" subtitle="Add your email and a password to get started." onSubmit={submit}>
      {error && <div className="banner banner-bad" role="alert">{error}</div>}

      <div className="auth-field">
        <label htmlFor="reg-email">Email Address</label>
        <input
          id="reg-email" type="email" value={email} onChange={(e) => setEmail(e.target.value)}
          placeholder="name@company.com" autoComplete="username" required autoFocus
        />
      </div>

      <div className="auth-field">
        <label htmlFor="reg-password">Password</label>
        <input
          id="reg-password" type="password" value={password} onChange={(e) => setPassword(e.target.value)}
          placeholder="At least 10 characters" autoComplete="new-password" minLength={10} required
        />
      </div>

      <button type="submit" disabled={busy}>{busy ? "Creating…" : "Create account"}</button>

      <p className="auth-foot">
        Already registered? <Link to="/login">Sign in</Link> · <Link to="/guest">Browse as guest</Link>
      </p>
    </AuthShell>
  );
}
