import { useEffect, useRef, useState } from "react";

/**
 * Counts rows that arrive while the page is open.
 *
 * The backend polls job boards on its own clock, so an open tab can hold rows
 * that are minutes old. Refetching fixes the data but shuffles the list under
 * whoever is reading it; this tracks row ids so the page can say what arrived
 * instead of silently moving things around.
 *
 * `scope` resets the baseline (filters, page) — otherwise switching views
 * reports every visible row as new.
 */
export function useLiveRows(
  rows: { id: number }[] | undefined,
  scope: unknown,
): { newCount: number; dismiss: () => void } {
  const seen = useRef<Set<number> | null>(null);
  const [newCount, setNewCount] = useState(0);

  useEffect(() => {
    seen.current = null;
    setNewCount(0);
  }, [scope]);

  useEffect(() => {
    if (!rows) return;
    const current = new Set(rows.map((row) => row.id));
    if (seen.current === null) {
      seen.current = current;
      return;
    }
    let fresh = 0;
    for (const id of current) {
      if (!seen.current.has(id)) fresh += 1;
    }
    seen.current = current;
    if (fresh > 0) setNewCount((count) => count + fresh);
  }, [rows]);

  return { newCount, dismiss: () => setNewCount(0) };
}
