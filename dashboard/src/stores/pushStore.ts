import AsyncStorage from '@react-native-async-storage/async-storage';
import { create } from 'zustand';
import { getPushSupport, pushPlatform, requestPushToken } from '../push/pushClient';
import { fetchPushMutes, fetchPushStatus, mutePush, registerPush, unmutePush, unregisterPush } from '../services/push.service';
import { useDeviceStore } from './deviceStore';
import type { PushRegistration } from '../types/push';

const REGISTRATION_KEY = 'sb_push_registration';
const TOKEN_KEY = 'sb_push_token';
const MUTED_KEY = 'sb_push_muted_alerts';

interface PushState {
  ready: boolean;
  busy: boolean;
  token: string | null;
  registration: PushRegistration | null;
  pushEnabled: boolean;
  confirmation: string | null;
  error: string | null;
  mutedAlertIds: Record<string, true>;
  init: () => Promise<void>;
  enable: () => Promise<PushRegistration>;
  reset: () => void;
  disable: () => Promise<void>;
  syncToken: (token: string) => Promise<void>;
  refreshStatus: () => Promise<void>;
  mute: (alertId: string) => Promise<void>;
  unmute: (alertId: string) => Promise<void>;
}

export function confirmationText(reg: PushRegistration): string | null {
  if (reg.confirmationSent === true) return 'Test notification sent to this device';
  if (reg.confirmationSent === false) {
    return `Registered, but the test notification wasn't sent: ${reg.confirmationError ?? 'unknown reason'}`;
  }
  return null;
}

async function persistMuted(muted: Record<string, true>): Promise<void> {
  await AsyncStorage.setItem(MUTED_KEY, JSON.stringify(muted));
}

export const usePushStore = create<PushState>((set, get) => ({
  ready: false,
  busy: false,
  token: null,
  registration: null,
  pushEnabled: true,
  confirmation: null,
  error: null,
  mutedAlertIds: {},

  init: async () => {
    if (get().ready) return;
    const [regRaw, token, mutedRaw] = await Promise.all([
      AsyncStorage.getItem(REGISTRATION_KEY),
      AsyncStorage.getItem(TOKEN_KEY),
      AsyncStorage.getItem(MUTED_KEY),
    ]);
    let registration: PushRegistration | null = null;
    let mutedAlertIds: Record<string, true> = {};
    try {
      registration = regRaw ? (JSON.parse(regRaw) as PushRegistration) : null;
      mutedAlertIds = mutedRaw ? (JSON.parse(mutedRaw) as Record<string, true>) : {};
    } catch {
      registration = null;
    }
    set({ ready: true, token, registration, mutedAlertIds });

    // Re-read the token on every start: the web SDK has no refresh event and tokens can rotate.
    if (registration && getPushSupport().supported) {
      try {
        const fresh = await requestPushToken();
        // Registering is idempotent and re-creates a registration the relay pruned (e.g. after an FCM rejection).
        await get().syncToken(fresh);
      } catch {
        // permission revoked or offline; the Notifications screen shows the state
      }
    }
  },

  enable: async () => {
    set({ busy: true, error: null });
    try {
      const token = await requestPushToken();
      const registration = await registerPush({
        token,
        platform: pushPlatform,
        deviceName: useDeviceStore.getState().device,
      });
      await AsyncStorage.multiSet([
        [REGISTRATION_KEY, JSON.stringify(registration)],
        [TOKEN_KEY, token],
      ]);
      set({ token, registration, confirmation: confirmationText(registration) });
      return registration;
    } catch (e) {
      const message = e instanceof Error ? e.message : 'Could not register for push.';
      console.warn('[push] register failed:', e);
      set({ error: message });
      throw e;
    } finally {
      set({ busy: false });
    }
  },

  disable: async () => {
    set({ busy: true });
    try {
      const { token } = get();
      if (token) await unregisterPush(token);
      await AsyncStorage.multiRemove([REGISTRATION_KEY, TOKEN_KEY, MUTED_KEY]);
      set({ token: null, registration: null, mutedAlertIds: {} });
    } finally {
      set({ busy: false });
    }
  },

  reset: () => {
    void AsyncStorage.multiRemove([REGISTRATION_KEY, TOKEN_KEY, MUTED_KEY]);
    set({ token: null, registration: null, confirmation: null, error: null, mutedAlertIds: {}, busy: false });
  },

  syncToken: async (token) => {
    if (!get().registration) return;
    const registration = await registerPush({
      token,
      platform: pushPlatform,
      deviceName: useDeviceStore.getState().device,
    });
    await AsyncStorage.multiSet([
      [REGISTRATION_KEY, JSON.stringify(registration)],
      [TOKEN_KEY, token],
    ]);
    set({ token, registration, confirmation: confirmationText(registration) ?? get().confirmation });
  },

  refreshStatus: async () => {
    const { token } = get();
    if (!token) return;
    const status = await fetchPushStatus(token);
    if (!status.registered) {
      await AsyncStorage.removeItem(REGISTRATION_KEY);
      set({ registration: null });
      return;
    }
    const registration = {
      account: status.account ?? 'account',
      minSeverity: status.minSeverity,
      confirmationSent: null,
      confirmationError: null,
    };
    await AsyncStorage.setItem(REGISTRATION_KEY, JSON.stringify(registration));
    const muted = await fetchPushMutes(token).catch(() => null);
    const mutedAlertIds: Record<string, true> = {};
    for (const id of muted ?? Object.keys(get().mutedAlertIds)) mutedAlertIds[id] = true;
    await persistMuted(mutedAlertIds);
    set({ registration, pushEnabled: status.pushEnabled, mutedAlertIds });
  },

  mute: async (alertId) => {
    await mutePush(alertId);
    const next = { ...get().mutedAlertIds, [alertId]: true as const };
    set({ mutedAlertIds: next });
    await persistMuted(next);
  },

  unmute: async (alertId) => {
    await unmutePush(alertId);
    const next = { ...get().mutedAlertIds };
    delete next[alertId];
    set({ mutedAlertIds: next });
    await persistMuted(next);
  },
}));
