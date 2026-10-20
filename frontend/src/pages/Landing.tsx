import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import AuthPanel, { type AuthMode } from "./AuthPanel";

/**
 * Public marketing page with an embedded auth form. Signed-out visitors land
 * here instead of a login wall; clicking Login / Sign-Up keeps the tagline
 * in place and swaps the hero art (right side) for the auth form — part of
 * the page flow, on THIS same route (mode state, no navigation, no popup).
 * Modes:
 *
 *   marketing (default)  — nav/hero/CTA/footer, buttons float the panel
 *   ?mode=login          — login card over the landing page
 *   ?mode=register       — registration card over the landing page
 *
 * Old /login and /register URLs redirect here in App.tsx (with ?mode=), so
 * bookmarks and emailed links keep working. Layout in marketing mode:
 *
 *   1. Top nav      — brand + anchor links + login/signup
 *   2. Hero         — yellow "Powered by Advanced AI" pill, headline, CTA
 *   3. Bento grid   — Find Jobs / Tailor CVs / Score Roles / AI Interview Studio
 *   4. Infrastructure — "Powered by CockroachDB" + distributed-vector blurb
 *   5. Footer
 */

const NAV_ANCHORS = [
  { href: "#how-it-works", label: "How it Works" },
  { href: "#features", label: "Features" },
  { href: "#infrastructure", label: "Infrastructure" },
] as const;

export default function Landing() {
  // The nav's Login / Sign-Up buttons swap the page between marketing mode
  // and an embedded auth panel (same URL). ?mode=login|register deep-links
  // into a panel from anywhere (old /login bookmarks land on /?mode=login).
  const [params] = useSearchParams();
  const initial = params.get("mode") === "register" ? "register" : params.get("mode") === "login" ? "login" : null;
  const [panel, setPanel] = useState<AuthMode | null>(initial);
  // The card lives in the hero at the top of the page. The nav is sticky, so
  // it stays visible when the visitor has scrolled down — but the form does
  // not. Every open/switch therefore scrolls the window back up so the
  // auth form is actually in view wherever the click came from. The card
  // itself has no close affordance: it stays open until sign-in.
  const openPanel = (mode: AuthMode) => {
    setPanel(mode);
    window.scrollTo({ top: 0, behavior: "smooth" });
  };

  return (
    <div className="landing">
      <header className="landing-nav">
        <div className="landing-nav-inner">
          <Link to="/" className="brand" aria-label="ApplyCanary home">
            <span className="brand-name">ApplyCanary</span>
          </Link>
          <nav className="landing-nav-links" aria-label="Sections">
            {NAV_ANCHORS.map((a) => (
              <a key={a.href} href={a.href}>{a.label}</a>
            ))}
          </nav>
          <div className="landing-nav-actions">
            {/* Always shown — no Back state: clicking an already-open mode
                just scrolls the visitor back up to the form. */}
            <button type="button" className="landing-login" onClick={() => openPanel("login")}>Login</button>
            <button type="button" className="btn-primary landing-signup" onClick={() => openPanel("register")}>Sign Up</button>
          </div>
        </div>
      </header>

      <main>
        {/* ------------------------------------------------ Hero */}
        <section className="landing-hero">
          <div className="landing-hero-copy">
            <span className="landing-eyebrow">
              <span className="material-symbols-outlined" aria-hidden="true">graphic_eq</span>
              Powered by Advanced AI
            </span>
            <h1>
              Your career agent,
              <br />
              with a{" "}
              <span className="landing-hero-accent">photographic memory.</span>
            </h1>
            <div className="landing-hero-rule" aria-hidden="true" />
            <p className="landing-lede">
              ApplyCanary intelligently analyzes your professional history, aligns
              it with market demands, and proactively secures interviews for roles
              you actually want.
            </p>
            <div className="landing-cta-row">
              <button type="button" className="btn-primary landing-cta" onClick={() => openPanel("register")}>
                Get Started
                <span className="material-symbols-outlined" aria-hidden="true">arrow_forward</span>
              </button>
              {/* Design pack home links out to the public events & paid
                  programs screen from here. */}
              <Link to="/opportunities" className="landing-cta-secondary">
                Explore events, internships &amp; programs
                <span className="material-symbols-outlined" aria-hidden="true">arrow_forward</span>
              </Link>
            </div>
          </div>

          {/* The right slot (the computer art) swaps for the auth card while
              Login/Sign-Up is open: the form is part of the page flow beside
              the marketing copy — not a popup, and the words stay put. */}
          {panel !== null ? (
          <div className="landing-hero-auth">
            <AuthPanel
              mode={panel}
              onModeChange={(m) => openPanel(m)}
            />
          </div>
          ) : (
          <div className="landing-hero-art" aria-hidden="true">
            <div className="landing-art-stage">
              <div className="landing-art-laptop">
                <div className="landing-art-screen">
                  <span className="landing-art-spark" />
                  <span className="landing-art-spark" />
                  <span className="landing-art-spark" />
                </div>
                <div className="landing-art-base" />
              </div>
              <div className="landing-art-orbit">
                <span className="landing-art-node" style={{ ["--a" as string]: "20deg" }} />
                <span className="landing-art-node" style={{ ["--a" as string]: "110deg" }} />
                <span className="landing-art-node" style={{ ["--a" as string]: "200deg" }} />
                <span className="landing-art-node" style={{ ["--a" as string]: "290deg" }} />
              </div>
              <div className="landing-art-core" />
            </div>
          </div>
          )}
        </section>

        {/* ------------------------------------------------ How it works

            Real anchor target for the nav's "How it Works" link: the hero
            CTA promised it and the marketing copy described it, but no such
            section existed, so the link did nothing. Three steps, plain
            cards so the whole strip stacks cleanly on a phone. */}
        <section className="landing-section" id="how-it-works" aria-labelledby="landing-how-h">
          <h2 id="landing-how-h" className="landing-section-title">How it works</h2>
          <p className="landing-section-sub">
            Three steps from browsing to interview-ready.
          </p>
          <div className="landing-steps">
            <article className="landing-step">
              <span className="landing-step-no" aria-hidden="true">1</span>
              <h3>Connect your profile</h3>
              <p>Drop in your resume and target roles — the canary learns what you want.</p>
            </article>
            <article className="landing-step">
              <span className="landing-step-no" aria-hidden="true">2</span>
              <h3>Get matched &amp; tailored</h3>
              <p>Postings are scored against your resume, and your CV is rewritten per job.</p>
            </article>
            <article className="landing-step">
              <span className="landing-step-no" aria-hidden="true">3</span>
              <h3>Practice &amp; land it</h3>
              <p>Run AI mock interviews with real speech, then apply with confidence.</p>
            </article>
          </div>
        </section>

        {/* ------------------------------------------------ Features (bento) */}
        <section className="landing-section" id="features" aria-labelledby="landing-features-h">
          <h2 id="landing-features-h" className="landing-section-title">Intelligent Features</h2>
          <p className="landing-section-sub">
            Everything you need to navigate your career trajectory with precision.
          </p>

          <div className="landing-bento">
            <article className="landing-cell landing-cell-wide">
              <span className="landing-cell-icon" aria-hidden="true">
                <span className="material-symbols-outlined">work_search</span>
              </span>
              <h3>Find Jobs</h3>
              <p>
                Our vector-powered search engine discovers hidden opportunities
                that match your unique skill fingerprint, not just keywords.
              </p>
              {/* Mini job-row from the mockup */}
              <div className="landing-cell-demo" aria-hidden="true">
                <div className="landing-demo-avatar">JD</div>
                <div className="landing-demo-text">
                  <div className="landing-demo-title">Senior Product Designer</div>
                  <div className="landing-demo-sub">Tech Innovations Inc. · Remote</div>
                </div>
                <span className="landing-demo-match">
                  <span className="material-symbols-outlined" aria-hidden="true">bolt</span>
                  95% Match
                </span>
              </div>
            </article>

            <article className="landing-cell">
              <span className="landing-cell-icon" aria-hidden="true">
                <span className="material-symbols-outlined">content_cut</span>
              </span>
              <h3>Tailor CVs</h3>
              <p>
                Instantly rewrite your experience to speak directly to specific
                job requirements.
              </p>
              <div className="landing-cell-demo landing-cell-demo-tailor" aria-hidden="true">
                <div className="landing-tailor-bar" />
                <span className="landing-tailor-label">Optimizing for ATS…</span>
              </div>
            </article>

            <article className="landing-cell">
              <span className="landing-cell-icon" aria-hidden="true">
                <span className="material-symbols-outlined">target</span>
              </span>
              <h3>Score Roles</h3>
              <p>
                Analyze job descriptions against your profile to predict interview
                success likelihood.
              </p>
              <div className="landing-cell-demo landing-cell-demo-bars" aria-hidden="true">
                <span className="landing-bar" style={{ ["--h" as string]: "42%" }} />
                <span className="landing-bar landing-bar-canary" style={{ ["--h" as string]: "88%" }} />
                <span className="landing-bar" style={{ ["--h" as string]: "60%" }} />
              </div>
            </article>

            <article className="landing-cell landing-cell-interview">
              <div>
                <span className="landing-cell-icon" aria-hidden="true">
                  <span className="material-symbols-outlined">headset_mic</span>
                </span>
                <h3>AI Interview Studio</h3>
                <p>
                  Practice with realistic voice agents trained on actual interview
                  questions for your target roles.
                </p>
              </div>
              {/* "Agent listening" waveform panel from the mockup */}
              <div className="landing-wave" aria-hidden="true">
                <div className="landing-wave-bars">
                  {Array.from({ length: 14 }, (_, i) => (
                    <span key={i} style={{ ["--d" as string]: `${i * 0.09}s` }} />
                  ))}
                </div>
                <span className="landing-wave-label">Agent Listening…</span>
              </div>
            </article>
          </div>
        </section>

        {/* ------------------------------------------------ Infrastructure */}
        <section className="landing-section" id="infrastructure" aria-labelledby="landing-infra-h">
          <div className="landing-pill-divider" aria-hidden="true">Infrastructure</div>
          <h2 id="landing-infra-h" className="landing-section-title">Powered by CockroachDB</h2>
          <p className="landing-section-sub">
            Resilient, distributed vector indexing ensures your career data is
            secure, always available, and blazingly fast.
          </p>
          <div className="landing-infra-card">
            <div className="landing-infra-copy">
              <h3>Distributed Vector Architecture</h3>
              <p>
                Your resume and every posting are embedded into a 1024-dimension
                vector space, indexed across replicas with cosine search — so
                matches surface in milliseconds, survive node failures, and your
                interview history is recalled semantically before every practice
                session.
              </p>
              <ul className="landing-infra-list">
                <li>Vector nodes replicate across regions</li>
                <li>Cosine-similarity ranking, no brute force</li>
                <li>Transactional state, audited agent access</li>
              </ul>
            </div>
            {/* Stylized distributed-vector diagram (CSS, no assets) */}
            <div className="landing-infra-diagram" aria-hidden="true">
              <div className="landing-infra-core">
                <span className="material-symbols-outlined">deployed_code</span>
                <span>Primary Vector Node</span>
              </div>
              <div className="landing-infra-nodes">
                {["Vector Node", "Vector Node", "Vector Node", "Vector Node", "Vector Node", "Vector Node"].map((label, i) => (
                  <div key={i} className="landing-infra-node">{label}</div>
                ))}
              </div>
            </div>
          </div>
        </section>

        {/* Closing CTA swaps panel instead of linking away */}
        <section className="landing-closing">
          <h2>Your next interview is already posted.</h2>
          <p>Let the canary find it — and get you ready to sing.</p>
          <div className="landing-cta-row landing-cta-row-center">
            <button type="button" className="btn-ai landing-cta" onClick={() => openPanel("register")}>
              Create free account
            </button>
          </div>
        </section>

      </main>

      <footer className="landing-footer">
        <span>© {new Date().getFullYear()} ApplyCanary. Professional Career Agent.</span>
        <span className="landing-footer-links">
          <Link to="/opportunities">Events, internships &amp; programs</Link>
          <Link to="/?mode=login">Sign in</Link>
          <Link to="/?mode=register">Create account</Link>
        </span>
      </footer>
    </div>
  );
}
