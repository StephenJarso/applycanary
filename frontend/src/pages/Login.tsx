import { useState, type FormEvent } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useAuth } from "../context/AuthContext";

export default function Login() {
  const { login } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await login(email, password);
      navigate("/", { replace: true });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Invalid credentials");
    } finally {
      setBusy(false);
    }
  }

  return <main className="auth-page"><form className="auth-card" onSubmit={submit}>
    <h1>ApplyCanary</h1><p>Sign in to your job dashboard.</p>
    {error && <div className="banner banner-bad" role="alert">{error}</div>}
    <label>Email or username<input type="text" value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="username" required autoFocus /></label>
    <label>Password<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" required /></label>
    <button disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
    <p className="cell-dim">Have an invite? <Link to="/register">Create an account</Link> · <Link to="/guest">Browse as guest</Link></p>
  </form></main>;
}

