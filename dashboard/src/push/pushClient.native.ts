import { Linking, Platform } from 'react-native';
import * as Notifications from 'expo-notifications';
import * as Device from 'expo-device';
import {
  getMessaging,
  getToken,
  onMessage,
  onNotificationOpenedApp,
  onTokenRefresh,
  getInitialNotification,
} from '@react-native-firebase/messaging';
import { ensureAlertChannels, presentAlert } from './channels.native';
import { channelIdFor, DEFAULT_CHANNEL_ID, KINDS, SEVERITIES } from './sounds';
import type { PushChannelInfo, PushOpenTarget, PushPermissionState, PushPlatform, PushSupport } from '../types/push';

export const pushPlatform: PushPlatform = Platform.OS === 'ios' ? 'ios' : 'android';

export function getPushSupport(): PushSupport {
  if (!Device.isDevice && Platform.OS === 'ios') {
    return { supported: false, reason: 'iOS push needs a real device.' };
  }
  return { supported: true };
}

function targetFrom(data: unknown): PushOpenTarget {
  const d = (data ?? {}) as Record<string, unknown>;
  const type = typeof d.type === 'string' ? d.type : null;
  if (typeof d.alertId === 'string' && d.alertId) return { alertId: d.alertId, type };
  if (typeof d.url === 'string') {
    const m = /[?&]alertId=([^&]+)/.exec(d.url);
    if (m) return { alertId: decodeURIComponent(m[1]), type };
  }
  return { alertId: null, type };
}

export async function getPermissionState(): Promise<PushPermissionState> {
  const p = await Notifications.getPermissionsAsync();
  return { status: p.granted ? 'granted' : p.canAskAgain && p.status === 'undetermined' ? 'undetermined' : 'denied', canAskAgain: p.canAskAgain };
}

export async function getChannelInfo(): Promise<PushChannelInfo | null> {
  if (Platform.OS !== 'android') return null;
  const channels = await Promise.all([
    ...SEVERITIES.flatMap((severity) => KINDS.map((kind) => Notifications.getNotificationChannelAsync(channelIdFor({ severity, kind })))),
    Notifications.getNotificationChannelAsync(DEFAULT_CHANNEL_ID),
  ]);
  if (channels.some((c) => !c)) return { ok: false, detail: 'Alert channels not created yet' };
  const present = channels.filter((c): c is Notifications.NotificationChannel => !!c);
  const muted = present.filter((c) => !c.sound || c.importance <= Notifications.AndroidImportance.LOW).length;
  const lowered = present.filter((c) => c.id.includes('_first') && c.importance < Notifications.AndroidImportance.HIGH).length;
  if (muted === 0 && lowered === 0) return { ok: true, detail: '11 alert channels, sound and vibration on' };
  const parts = [];
  if (muted) parts.push(`${muted} channel(s) silenced in system settings`);
  if (lowered) parts.push(`${lowered} alert channel(s) below high importance`);
  return { ok: false, detail: parts.join(', ') };
}

export function openSystemSettings(): void {
  void Linking.openSettings();
}

export async function requestPushToken(): Promise<string> {
  await ensureAlertChannels();
  const existing = await Notifications.getPermissionsAsync();
  let granted = existing.granted;
  if (!granted && existing.canAskAgain) {
    granted = (
      await Notifications.requestPermissionsAsync({
        ios: { allowAlert: true, allowSound: true, allowBadge: true, allowProvisional: false },
      })
    ).granted;
  }
  if (!granted) {
    throw new Error('Notification permission is off for this app. Turn it on in system settings (Notifications) to receive alerts.');
  }
  return getToken(getMessaging());
}

export function subscribeTokenRefresh(cb: (token: string) => void): () => void {
  return onTokenRefresh(getMessaging(), cb);
}

export function setupPushListeners(onOpen: (target: PushOpenTarget) => void): () => void {
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowBanner: true,
      shouldShowList: true,
      shouldPlaySound: true,
      shouldSetBadge: false,
    }),
  });
  void ensureAlertChannels();

  const messaging = getMessaging();

  // FCM does not draw notifications while the app is foregrounded; mirror them locally.
  const offMessage = onMessage(messaging, async (msg) => {
    const n = msg.notification;
    if (!n) return;
    // expo-notifications' own FCM service may already have drawn it; skip when an identical one is showing.
    await new Promise((r) => setTimeout(r, 500));
    const shown = await Notifications.getPresentedNotificationsAsync();
    if (shown.some((s) => s.request.content.title === (n.title ?? 'Alert') && s.request.content.body === (n.body ?? ''))) return;
    await presentAlert(n.title ?? 'Alert', n.body ?? '', (msg.data ?? {}) as Record<string, unknown>);
  });
  const offOpened = onNotificationOpenedApp(messaging, (msg) => {
    onOpen(targetFrom(msg.data));
  });
  void getInitialNotification(messaging).then((msg) => {
    if (msg) onOpen(targetFrom(msg.data));
  });
  const sub = Notifications.addNotificationResponseReceivedListener((resp) => {
    onOpen(targetFrom(resp.notification.request.content.data));
  });

  return () => {
    offMessage();
    offOpened();
    sub.remove();
  };
}
