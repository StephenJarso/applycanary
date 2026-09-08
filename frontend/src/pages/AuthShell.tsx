import type { FormEvent, ReactNode } from "react";

/** The briefcase mark from the auth mockup. */
function Briefcase() {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="7" width="20" height="14" rx="2" />
      <path d="M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16" />
    </svg>
  );
}

/**
 * Shared chrome for the auth screens — glow background, centred card, icon
 * tile, heading and subtitle. Everything below the subtitle is the caller's, so
 * a form and an outcome message can share the same shell.
 */
export default function AuthShell({
  title,
  subtitle,
  glyph,
  tone,
  onSubmit,
  children,
}: {
  title: string;
  subtitle?: ReactNode;
  /** Replaces the brand mark on outcome screens, e.g. "✓" or "!". */
  glyph?: ReactNode;
  tone?: "brand" | "bad";
  /** Renders a <form> when given, a <div> otherwise (outcome screens). */
  onSubmit?: (event: FormEvent) => void;
  children: ReactNode;
}) {
  const body = (
    <>
      <div className={`auth-mark${tone === "bad" ? " is-bad" : ""}`} aria-hidden="true">
        {glyph ?? <Briefcase />}
      </div>
      <h1>{title}</h1>
      {subtitle && <p className="auth-sub">{subtitle}</p>}
      {children}
    </>
  );

  return (
    <main className="auth-page">
      {onSubmit
        ? <form className="auth-card" onSubmit={onSubmit}>{body}</form>
        : <div className="auth-card">{body}</div>}
    </main>
  );
}
