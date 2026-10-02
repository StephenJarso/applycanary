import { Capacitor } from "@capacitor/core";

/**
 * Native-shell bootstrap. Runs once before React mounts (see main.tsx).
 *
 * On iOS the WebView origin is capacitor://localhost and on Android (with the
 * https scheme) https://localhost — both are custom origins the FastAPI
 * backend must explicitly allow in CORS, and must echo credentials for, or
 * the session cookie silently fails to stick. CapacitorHttp patches fetch to
 * bypass the WebView CORS check entirely by routing requests through native
 * HTTP; we only enable it when a remote API base is configured.
 */
export async function initNative(): Promise<void> {
  if (!Capacitor.isNativePlatform()) return;

  const base = localStorage.getItem("ac.api-base") || import.meta.env?.VITE_API_BASE || "";
  if (!base) {
    console.warn(
      "[ApplyCanary] No VITE_API_BASE set — API calls will fail in the native shell. " +
        "Rebuild with VITE_API_BASE=https://your-backend (or set localStorage['ac.api-base']).",
    );
    return;
  }

  // Route fetch through native HTTP (no CORS preflights from the WebView).
  const w = window as unknown as { CapacitorHttp?: { patchFetch?: () => void } };
  w.CapacitorHttp?.patchFetch?.();
}
