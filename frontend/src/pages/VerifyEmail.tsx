import { useEffect, useRef, useState } from "react";
import { Link, useLocation, useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../context/AuthContext";
import AuthShell from "./AuthShell";

type State = "working" | "done" | "failed";

/**
 * Lands the confirmation links from email. Serves both /verify-email (new
 * signup) and /verify-email-change (a staged address change), which differ only
 * in the endpoint called and the wording.
 */
export default function VerifyEmail() {
  const [params] = useSearchParams();
  const { pathname } = useLocation();
  const { refresh } = useAuth();
  const token = params.get("token") ?? "";
  const isChange = pathname.startsWith("/verify-email-change");

  const [state, setState] = useState<State>(token ? "working" : "failed");
  const [error, setError] = useState(token ? "" : "This link is missing its token.");
  // The token is single-use, so a second POST always fails. StrictMode runs
  // effects twice in development, which would consume the token and then report
  // the retry's 400 to the user — hence the guard rather than a bare effect.
  const submitted = useRef(false);

  useEffect(() => {
    if (!token || submitted.current) return;
    submitted.current = true;
    const verify = isChange ? api.auth.verifyEmailChange : api.auth.verifyEmail;
    void verify(token)
      .then(async () => {
        setState("done");
        // Clears the "confirm your email" nudge if this tab holds a session.
        await refresh().catch(() => { /* not signed in here; nothing to refresh */ });
      })
      .catch((err: unknown) => {
        setState("failed");
        setError(err instanceof Error ? err.message : "That link is invalid or has expired.");
      });
  }, [token, isChange, refresh]);

  if (state === "working") {
    return (
      <AuthShell title="Confirming your email" subtitle="One moment…">
        <p className="auth-foot"><span className="spinner" /></p>
      </AuthShell>
    );
  }

  if (state === "done") {
    return (
      <AuthShell
        title={isChange ? "Email address updated" : "Email confirmed"}
        subtitle={isChange
          ? "Your account now uses your new address. For safety, other devices have been signed out."
          : "Thanks — your address is confirmed and your account is active."}
        glyph="✓"
      >
        <Link className="auth-submit" to="/login">Continue to sign in</Link>
      </AuthShell>
    );
  }

  return (
    <AuthShell title="This link didn't work" subtitle={error} glyph="!" tone="bad">
      <p className="auth-foot">
        Confirmation links expire after 24 hours. Sign in to have a fresh one sent.
      </p>
      <Link className="auth-submit" to="/login">Back to sign in</Link>
    </AuthShell>
  );
}
