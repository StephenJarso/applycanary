import type { Opportunity } from "./api";
import { daysUntil, fmtDate } from "./OpportunityCard";

export interface DeadlineItem {
  title: string;
  org: string;
  kind: Opportunity["kind"];
  deadline: string;
  daysLeft: number;
  url: string;
}

const STORAGE_KEY_LAST_NOTIFIED = "ac.deadlines.last_notified";
const STORAGE_KEY_DISMISSED = "ac.deadlines.banner_dismissed";

export function getUpcomingDeadlines(items: Opportunity[], withinDays = 7): DeadlineItem[] {
  const result: DeadlineItem[] = [];
  for (const item of items) {
    const rawDate = item.closes || (item.kind === "Event" ? item.date : null);
    if (!rawDate) continue;
    const days = daysUntil(rawDate);
    if (days >= 0 && days <= withinDays) {
      result.push({
        title: item.title,
        org: item.org,
        kind: item.kind,
        deadline: rawDate,
        daysLeft: days,
        url: item.url,
      });
    }
  }
  return result.sort((a, b) => a.daysLeft - b.daysLeft);
}

export function isNotificationSupported(): boolean {
  return typeof window !== "undefined" && "Notification" in window;
}

export function getNotificationPermission(): NotificationPermission | "unsupported" {
  if (!isNotificationSupported()) return "unsupported";
  return Notification.permission;
}

export async function requestNotificationPermission(): Promise<NotificationPermission | "unsupported"> {
  if (!isNotificationSupported()) return "unsupported";
  try {
    return await Notification.requestPermission();
  } catch {
    return Notification.permission;
  }
}

export function triggerLocalDeadlineNotification(upcoming: DeadlineItem[]): boolean {
  if (!isNotificationSupported() || Notification.permission !== "granted") return false;
  if (!upcoming.length) return false;

  const first = upcoming[0];
  if (!first) return false;
  const title = `ApplyCanary: ${upcoming.length === 1 ? "Upcoming Deadline" : `${upcoming.length} Upcoming Deadlines`}`;
  let body: string;

  if (upcoming.length === 1) {
    const timeStr = first.daysLeft === 0 ? "closes today" : `closes in ${first.daysLeft} day${first.daysLeft > 1 ? "s" : ""}`;
    body = `${first.title} (${first.org}) ${timeStr} (${fmtDate(first.deadline)}).`;
  } else {
    const timeStr = first.daysLeft === 0 ? "today" : `in ${first.daysLeft}d`;
    body = `Nearest: ${first.title} (${timeStr}) + ${upcoming.length - 1} more closing within 7 days.`;
  }

  try {
    const notif = new Notification(title, {
      body,
      icon: "/assets/icon.png",
      tag: "applycanary-deadlines",
    });
    notif.onclick = () => {
      window.focus();
      if (first.url && first.url.startsWith("http")) {
        window.open(first.url, "_blank");
      }
    };
    return true;
  } catch (err) {
    console.warn("Failed to create local notification:", err);
    return false;
  }
}

export function checkAndNotifyDailyDeadlines(
  items: Opportunity[],
  force = false,
): { notified: boolean; upcoming: DeadlineItem[] } {
  const upcoming = getUpcomingDeadlines(items, 7);
  if (!upcoming.length) return { notified: false, upcoming: [] };

  const today = new Date().toISOString().slice(0, 10);
  const lastNotified = localStorage.getItem(STORAGE_KEY_LAST_NOTIFIED);

  if (!force && lastNotified === today) {
    return { notified: false, upcoming };
  }

  if (isNotificationSupported() && Notification.permission === "granted") {
    const sent = triggerLocalDeadlineNotification(upcoming);
    if (sent) {
      localStorage.setItem(STORAGE_KEY_LAST_NOTIFIED, today);
      return { notified: true, upcoming };
    }
  }

  return { notified: false, upcoming };
}

export function isDeadlineBannerDismissedToday(): boolean {
  const today = new Date().toISOString().slice(0, 10);
  return localStorage.getItem(STORAGE_KEY_DISMISSED) === today;
}

export function dismissDeadlineBannerToday(): void {
  const today = new Date().toISOString().slice(0, 10);
  localStorage.setItem(STORAGE_KEY_DISMISSED, today);
}
