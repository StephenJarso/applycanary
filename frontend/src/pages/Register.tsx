import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export default function Register() {
  const { register } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [inviteCode, setInviteCode] = useState(() => new URLSearchParams(window.location.search).get("invite_code") ?? "");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await register(email, password, inviteCode);
      navigate("/profile", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Registration failed");
    } finally {
      setBusy(false);
    }
  }

  return <main className="auth-page"><form className="auth-card" onSubmit={submit}>
    <h1>Create account</h1><p>Use your single-use invite code.</p>
    {error && <div className="banner banner-bad" role="alert">{error}</div>}
    <label>Email<input type="email" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required autoFocus /></label>
    <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" minLength={10} required /></label>
    <label>Invite code<input value={inviteCode} onChange={(e) => setInviteCode(e.target.value)} autoComplete="off" required /></label>
    <button disabled={busy}>{busy ? "Creating…" : "Create account"}</button>
    <p className="cell-dim">Already registered? <Link to="/login">Sign in</Link> · <Link to="/guest">Browse as guest</Link></p>
  </form></main>;
}
