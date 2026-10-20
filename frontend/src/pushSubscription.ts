/**
 * Web Push subscription helpers.
 * Registers the service worker, subscribes to push, and syncs with the backend.
 */

import { API_BASE } from "./api";

const SW_PATH = "/sw.js";

export function isPushSupported(): boolean {
  return (
    typeof window !== "undefined" &&
    "serviceWorker" in navigator &&
    "PushManager" in window
  );
}

export async function getVapidPublicKey(): Promise<string> {
  try {
    const res = await fetch(`${API_BASE}/api/push/vapid-public-key`);
    const data = (await res.json()) as { publicKey?: string };
    return data.publicKey ?? "";
  } catch {
    return "";
  }
}

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = "=".repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, "+").replace(/_/g, "/");
  const raw = atob(base64);
  return Uint8Array.from([...raw].map((c) => c.charCodeAt(0)));
}

export async function subscribeToPush(): Promise<boolean> {
  if (!isPushSupported()) return false;
  try {
    const publicKey = await getVapidPublicKey();
    if (!publicKey) return false;

    const reg = await navigator.serviceWorker.register(SW_PATH);
    await navigator.serviceWorker.ready;

    const permission = await Notification.requestPermission();
    if (permission !== "granted") return false;

    const sub = await reg.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(publicKey),
    });

    const json = sub.toJSON();
    const keys = (json.keys ?? {}) as { p256dh?: string; auth?: string };

    await fetch(`${API_BASE}/api/push/subscribe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        endpoint: sub.endpoint,
        p256dh: keys.p256dh ?? "",
        auth: keys.auth ?? "",
      }),
    });
    return true;
  } catch (err) {
    console.warn("Push subscribe failed:", err);
    return false;
  }
}

export async function unsubscribeFromPush(): Promise<void> {
  try {
    const reg = await navigator.serviceWorker.getRegistration(SW_PATH);
    if (reg) {
      const sub = await reg.pushManager.getSubscription();
      if (sub) await sub.unsubscribe();
    }
    await fetch(`${API_BASE}/api/push/unsubscribe`, { method: "DELETE" });
  } catch (err) {
    console.warn("Push unsubscribe failed:", err);
  }
}

export async function getExistingSubscription(): Promise<PushSubscription | null> {
  if (!isPushSupported()) return null;
  try {
    const reg = await navigator.serviceWorker.getRegistration(SW_PATH);
    if (!reg) return null;
    return await reg.pushManager.getSubscription();
  } catch {
    return null;
  }
}
