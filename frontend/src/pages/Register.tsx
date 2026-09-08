import { useEffect, useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";
import { api } from "../api";
import AuthShell from "./AuthShell";

export default function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [inviteCode, setInviteCode] = useState(() => new URLSearchParams(window.location.search).get("invite_code") ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  // Set when the account was created but no session was issued, because the
  // deployment requires the address to be confirmed first.
  const [awaitingConfirmation, setAwaitingConfirmation] = useState(false);

  // Hackathon open-signup: prefill the invite code with the backend's shared
  // referral code so new users can register without hunting for an invite.
  useEffect(() => {
    if (inviteCode) return;
    let cancelled = false;
    api.auth.signupInfo()
      .then((info) => { if (!cancelled && info.default_invite_code) setInviteCode(info.default_invite_code); })
      .catch(() => { /* endpoint missing or disabled — leave the field blank */ });
    return () => { cancelled = true; };
  }, [inviteCode]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      const created = await register(email, password, inviteCode);
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
    <AuthShell title="Create your account" subtitle="Your invite code is prefilled — just add your email and password." onSubmit={submit}>
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

      <div className="auth-field">
        <label htmlFor="reg-invite">Invite code</label>
        <input id="reg-invite" value={inviteCode} onChange={(e) => setInviteCode(e.target.value)} autoComplete="off" required />
      </div>

      <button type="submit" disabled={busy}>{busy ? "Creating…" : "Create account"}</button>

      <p className="auth-foot">
        Already registered? <Link to="/login">Sign in</Link> · <Link to="/guest">Browse as guest</Link>
      </p>
    </AuthShell>
  );
}
