import type { ReactNode } from "react";
import type { Score } from "./api";

/** Score pill. Thresholds match the verdict bands the backend scorer uses. */
export function ScoreBadge({ score }: { score: Score | null }) {
  if (!score) return <span className="score score-none">—</span>;
  const cls =
    score.total >= 75 ? "score-strong" : score.total >= 55 ? "score-mid" : "score-weak";
  return (
    <span className={`score ${cls}`} title={score.verdict || undefined}>
      {Math.round(score.total)}
    </span>
  );
}

export function Chips({
  items,
  variant = "",
  max = 6,
}: {
  items: string[];
  variant?: "hit" | "miss" | "accent" | "";
  max?: number;
}) {
  if (!items.length) return null;
  const shown = items.slice(0, max);
  const rest = items.length - shown.length;
  return (
    <div className="chips">
      {shown.map((it) => (
        <span key={it} className={`chip${variant ? ` chip-${variant}` : ""}`}>
          {it}
        </span>
      ))}
      {rest > 0 && <span className="chip">+{rest}</span>}
    </div>
  );
}

export function Empty({ title, hint }: { title: string; hint?: ReactNode }) {
  return (
    <div className="empty">
      <div className="empty-title">{title}</div>
      {hint && <div>{hint}</div>}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return (
    <div className="empty" role="status" aria-live="polite">
      <div className="spinner" style={{ margin: "0 auto 10px" }} />
      {label}
    </div>
  );
}

export function ErrorBox({ error }: { error: unknown }) {
  const message = error instanceof Error ? error.message : String(error);
  return (
    <div className="banner banner-bad" role="alert">
      <span>{message}</span>
    </div>
  );
}

export function TableSkeleton({ rows = 8, cols = 6 }: { rows?: number; cols?: number }) {
  return (
    <div className="table-wrap" aria-hidden="true">
      <table>
        <tbody>
          {Array.from({ length: rows }, (_, r) => (
            <tr key={r}>
              {Array.from({ length: cols }, (_, c) => (
                <td key={c}>
                  <div className="skeleton" style={{ width: `${45 + ((r * 7 + c * 13) % 50)}%` }} />
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Compact relative time. Hours matter here — applying early is the point. */
export function relTime(iso: string | null): string {
  if (!iso) return "—";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "—";
  const mins = Math.round((Date.now() - then) / 60000);
  if (mins < 1) return "now";
  if (mins < 60) return `${mins}m`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h`;
  const days = Math.round(hours / 24);
  if (days < 30) return `${days}d`;
  return `${Math.round(days / 30)}mo`;
}

export function formatSalary(
  min: number | null,
  max: number | null,
  currency: string,
  isEstimate: boolean,
): string | null {
  if (min === null && max === null) return null;
  const fmt = (n: number) => (n >= 1000 ? `${Math.round(n / 1000)}k` : String(n));
  const range =
    min !== null && max !== null
      ? `${fmt(min)}–${fmt(max)}`
      : fmt((min ?? max) as number);
  return `${range} ${currency}${isEstimate ? " (est)" : ""}`.trim();
}
