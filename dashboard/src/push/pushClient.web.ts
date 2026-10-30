import { initializeApp, getApps } from 'firebase/app';
import { getMessaging, getToken, onMessage, isSupported } from 'firebase/messaging';
import {
  FIREBASE_MISSING,
  FIREBASE_VAPID_KEY,
  FIREBASE_WEB_CONFIG,
  FIREBASE_WEB_CONFIGURED,
  VAPID_PROBLEM,
} from '../config/firebaseWeb';
import { playPushSound } from './pushSound';
import type { PushChannelInfo, PushOpenTarget, PushPermissionState, PushPlatform, PushSupport } from '../types/push';

export const pushPlatform: PushPlatform = 'web';

export async function getPermissionState(): Promise<PushPermissionState> {
  if (typeof Notification === 'undefined') return { status: 'denied', canAskAgain: false };
  const p = Notification.permission;
  return { status: p === 'default' ? 'undetermined' : p, canAskAgain: p === 'default' };
}

export async function getChannelInfo(): Promise<PushChannelInfo | null> {
  return null;
}

export function openSystemSettings(): void {}

export function getPushSupport(): PushSupport {
  if (FIREBASE_MISSING.length > 0) {
    return {
      supported: false,
      reason: `Web push not configured: missing ${FIREBASE_MISSING.join(', ')} in dashboard/firebase/google-services-web.json (restart the dev server after editing it).`,
    };
  }
  if (VAPID_PROBLEM) {
    return {
      supported: false,
      reason: `Web push key ${VAPID_PROBLEM} in dashboard/firebase/google-services-web.json (field "vapidKey", from Firebase console > Cloud Messaging > Web Push certificates).`,
    };
  }
  if (typeof window === 'undefined' || !('serviceWorker' in navigator) || !('Notification' in window)) {
    return { supported: false, reason: 'This browser does not support web push.' };
  }
  if (!window.isSecureContext) return { supported: false, reason: 'Web push needs HTTPS or http://localhost.' };
  return { supported: true };
}

function messagingInstance() {
  const app = getApps()[0] ?? initializeApp(FIREBASE_WEB_CONFIG);
  return getMessaging(app);
}

function targetFrom(data: Record<string, string> | undefined): PushOpenTarget {
  const type = data?.type ?? null;
  if (data?.alertId) return { alertId: data.alertId, type };
  const m = data?.url ? /[?&]alertId=([^&]+)/.exec(data.url) : null;
  return { alertId: m ? decodeURIComponent(m[1]) : null, type };
}

export async function requestPushToken(): Promise<string> {
  const support = getPushSupport();
  if (!support.supported) throw new Error(support.reason);
  if (!(await isSupported())) throw new Error('This browser does not support web push.');
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') {
    throw new Error('Notification permission was denied. Allow it in the browser site settings.');
  }
  const swUrl = `/firebase-messaging-sw.js?c=${encodeURIComponent(JSON.stringify(FIREBASE_WEB_CONFIG))}`;
  const registration = await navigator.serviceWorker.register(swUrl, { scope: '/' });
  await navigator.serviceWorker.ready;
  const attempt = () =>
    getToken(messagingInstance(), { vapidKey: FIREBASE_VAPID_KEY, serviceWorkerRegistration: registration });
  try {
    return await attempt();
  } catch (first) {
    // A browser subscribed with a previous VAPID key rejects the new one until that subscription is dropped.
    try {
      const existing = await registration.pushManager.getSubscription();
      if (existing) {
        await existing.unsubscribe();
        return await attempt();
      }
    } catch (second) {
      first = second;
    }
    console.error('[push] getToken failed:', first);
    const detail = first instanceof Error ? first.message : String(first);
    throw new Error(`Could not get a web push token: ${detail}`);
  }
}

// The web SDK has no token-refresh event; the token is re-read on every app start instead.
export function subscribeTokenRefresh(_cb: (token: string) => void): () => void {
  return () => {};
}

export function setupPushListeners(onOpen: (target: PushOpenTarget) => void): () => void {
  if (!getPushSupport().supported) return () => {};
  const off = onMessage(messagingInstance(), (payload) => {
    playPushSound(payload.data);
    const n = payload.notification;
    if (!n || Notification.permission !== 'granted') return;
    const shown = new Notification(n.title ?? 'Alert', { body: n.body ?? '' });
    shown.onclick = () => {
      onOpen(targetFrom(payload.data));
      window.focus();
    };
  });
  const onSwMessage = (e: MessageEvent) => {
    if (e.data?.type === 'sb-sound') playPushSound(e.data.data);
    if (e.data?.type === 'sb-open') onOpen({ alertId: e.data.alertId ?? null, type: e.data.pushType ?? null });
  };
  navigator.serviceWorker.addEventListener('message', onSwMessage);
  return () => {
    off();
    navigator.serviceWorker.removeEventListener('message', onSwMessage);
  };
}
