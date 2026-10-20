import { Link, NavLink } from "react-router-dom";

/**
 * Sticky public top bar shared by the marketing page and the public
 * Internships / Programs screens. Signed-out visitors can jump between the
 * landing sections and either feed without losing the header navigation.
 */
const SECTIONS = [
  { href: "#how-it-works", label: "How it Works" },
  { href: "#features", label: "Features" },
  { href: "#infrastructure", label: "Infrastructure" },
] as const;

const PAGES = [
  { to: "/internships", label: "Internships" },
  { to: "/opportunities", label: "Programs" },
] as const;

type Props = {
  /** Landing itself scrolls in-place; other pages route back to its anchors. */
  localSections?: boolean;
  onLogin?: () => void;
  onSignup?: () => void;
};

export default function PublicNav({ localSections = false, onLogin, onSignup }: Props) {
  return (
    <header className="landing-nav">
      <div className="landing-nav-inner">
        <Link to="/" className="brand" aria-label="ApplyCanary home">
          <span className="brand-name">ApplyCanary</span>
        </Link>
        <nav className="landing-nav-links" aria-label="Sections">
          {SECTIONS.map((section) =>
            localSections ? (
              <a key={section.href} href={section.href}>{section.label}</a>
            ) : (
              <Link key={section.href} to={`/${section.href}`}>{section.label}</Link>
            ),
          )}
          {PAGES.map((page) => (
            <NavLink
              key={page.to}
              to={page.to}
              className={({ isActive }) => (isActive ? "active" : undefined)}
            >
              {page.label}
            </NavLink>
          ))}
        </nav>
        <div className="landing-nav-actions">
          {onLogin ? (
            <button type="button" className="landing-login" onClick={onLogin}>Login</button>
          ) : (
            <Link to="/?mode=login" className="landing-login">Login</Link>
          )}
          {onSignup ? (
            <button type="button" className="btn-primary landing-signup" onClick={onSignup}>Sign Up</button>
          ) : (
            <Link to="/?mode=register" className="btn-primary landing-signup">Sign Up</Link>
          )}
        </div>
      </div>
    </header>
  );
}
