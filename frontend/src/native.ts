import { Capacitor } from "@capacitor/core";
import { App as CapApp } from "@capacitor/app";
import { StatusBar, Style } from "@capacitor/status-bar";

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
  } else {
    // Route fetch through native HTTP (no CORS preflights from the WebView).
    const w = window as unknown as { CapacitorHttp?: { patchFetch?: () => void } };
    w.CapacitorHttp?.patchFetch?.();
  }

  // Mobile status bar styling
  try {
    await StatusBar.setStyle({ style: Style.Light });
    if (Capacitor.getPlatform() === "android") {
      await StatusBar.setOverlaysWebView({ overlay: false });
    }
  } catch (err) {
    console.debug("[ApplyCanary] StatusBar configuration skipped:", err);
  }

  // Native Android hardware back-button handling
  try {
    await CapApp.addListener("backButton", ({ canGoBack }) => {
      // Check if a modal is open
      const modalBackdrop = document.querySelector(".modal-backdrop") as HTMLElement | null;
      if (modalBackdrop) {
        modalBackdrop.click();
        return;
      }
      // Check if the more menu is open
      const moreCloseBtn = document.querySelector(".mobile-more-close") as HTMLButtonElement | null;
      if (moreCloseBtn) {
        moreCloseBtn.click();
        return;
      }
      if (canGoBack) {
        window.history.back();
      } else {
        void CapApp.exitApp();
      }
    });
  } catch (err) {
    console.debug("[ApplyCanary] BackButton listener skipped:", err);
  }
}
