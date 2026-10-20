/**
 * Render free-tier keep-alive and connectivity monitor.
 *
 * Render spins down free web instances after 15 minutes of inbound HTTP
 * inactivity, and waking a cold instance takes ~30–50 seconds.
 *
 * This module runs a lightweight background ping to `/health` every 45 seconds
 * while the app is open in a browser tab. It also detects when the backend is
 * waking up from idle and notifies listeners.
 */

import { API_BASE } from "./api";

const PING_INTERVAL_MS = 45_000;
let pingTimer: ReturnType<typeof setInterval> | null = null;
let lastPingSuccess = true;
let isWakingUp = false;

type Listener = (status: { online: boolean; wakingUp: boolean }) => void;
const listeners = new Set<Listener>();

function notify(): void {
  for (const fn of listeners) {
    try {
      fn({ online: lastPingSuccess, wakingUp: isWakingUp });
    } catch {
      /* ignore listener errors */
    }
  }
}

export async function pingBackend(): Promise<boolean> {
  const url = `${API_BASE}/health`;
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), 12_000);

  try {
    const res = await fetch(url, {
      method: "GET",
      signal: controller.signal,
      cache: "no-store",
    });
    clearTimeout(timeoutId);

    if (res.ok) {
      lastPingSuccess = true;
      isWakingUp = false;
      notify();
      return true;
    }

    // 502/503/504 typically means the reverse proxy is waiting on the container to boot
    if ([502, 503, 504].includes(res.status)) {
      isWakingUp = true;
      lastPingSuccess = false;
      notify();
    }
    return false;
  } catch {
    clearTimeout(timeoutId);
    // Network abort or failed fetch: likely spinning up or offline
    isWakingUp = true;
    lastPingSuccess = false;
    notify();
    return false;
  }
}

export function startKeepAlive(): () => void {
  if (typeof window === "undefined") return () => {};

  if (!pingTimer) {
    // Initial ping shortly after boot
    setTimeout(() => void pingBackend(), 2000);
    pingTimer = setInterval(() => {
      // Only ping if tab is active/visible, or ping at reduced rate when hidden
      if (!document.hidden) {
        void pingBackend();
      }
    }, PING_INTERVAL_MS);

    // Wake-up check when tab regains focus
    window.addEventListener("focus", () => void pingBackend());
  }

  return () => {
    if (pingTimer) {
      clearInterval(pingTimer);
      pingTimer = null;
    }
  };
}

export function subscribeServerHealth(fn: Listener): () => void {
  listeners.add(fn);
  fn({ online: lastPingSuccess, wakingUp: isWakingUp });
  return () => listeners.delete(fn);
}
