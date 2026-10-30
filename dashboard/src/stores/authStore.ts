import { create } from 'zustand';
import { clearSession, loadSession, saveSession } from '../api/sessionStorage';
import { onSessionInvalid, setSessionToken } from '../api/session';
import { loginRequest, logoutRequest, meRequest } from '../services/auth.service';
import { getPermissionState, pushPlatform } from '../push/pushClient';
import { useDeviceStore } from './deviceStore';
import { usePushStore } from './pushStore';
import { useUIStore } from './uiStore';

export const SESSION_EXPIRED_TEXT = 'Session expired, sign in again';

interface AuthState {
  ready: boolean;
  token: string | null;
  account: string | null;
  minSeverity: number | null;
  error: string | null;
  init: () => Promise<void>;
  login: (accountSecret: string) => Promise<boolean>;
  logout: () => Promise<void>;
  expire: () => void;
  expireIfRevoked: () => Promise<void>;
  clearError: () => void;
}

export const useAuthStore = create<AuthState>((set, get) => ({
  ready: false,
  token: null,
  account: null,
  minSeverity: null,
  error: null,

  init: async () => {
    if (get().ready) return;
    await useDeviceStore.getState().init();
    let token: string | null = null;
    let account: string | null = null;
    let minSeverity: number | null = null;
    try {
      const raw = await loadSession();
      const parsed = raw ? (JSON.parse(raw) as { token?: string; account?: string; minSeverity?: number | null }) : null;
      if (parsed?.token) {
        token = parsed.token;
        account = parsed.account ?? null;
        minSeverity = parsed.minSeverity ?? null;
      }
    } catch {}
    if (token && (await meRequest(token)) === 'invalid') {
      await clearSession();
      set({ ready: true, error: SESSION_EXPIRED_TEXT });
      return;
    }
    setSessionToken(token);
    set({ ready: true, token, account, minSeverity });
  },

  login: async (accountSecret) => {
    const secret = accountSecret.trim();
    if (!secret) return false;
    try {
      const res = await loginRequest({
        accountSecret: secret,
        deviceName: useDeviceStore.getState().device,
        deviceId: useDeviceStore.getState().deviceId,
        platform: pushPlatform,
      });
      await saveSession(JSON.stringify({ token: res.token, account: res.account, minSeverity: res.minSeverity }));
      setSessionToken(res.token);
      set({ token: res.token, account: res.account, minSeverity: res.minSeverity, error: null });
      // Push opt-in must never block sign-in (permission denied, no web config, offline).
      void usePushStore
        .getState()
        .enable()
        .then(() => {
          const text = usePushStore.getState().confirmation;
          if (text) useUIStore.getState().showToast(text.startsWith('Test') ? 'success' : 'warning', text);
        })
        .catch(async (e) => {
          // A denied permission is a user choice; the Background readiness card reports it.
          const permission = await getPermissionState().catch(() => null);
          if (permission?.status === 'denied') return;
          useUIStore.getState().showToast('warning', `Signed in, but push is not set up: ${e instanceof Error ? e.message : 'unknown error'}`);
        });
      return true;
    } catch (e) {
      set({ error: e instanceof Error ? e.message : 'Sign-in failed.' });
      return false;
    }
  },

  logout: async () => {
    const token = get().token;
    if (token) {
      await usePushStore.getState().disable().catch(() => {});
      await logoutRequest(token).catch(() => {});
    }
    await clearSession();
    usePushStore.getState().reset();
    setSessionToken(null);
    set({ token: null, account: null, minSeverity: null, error: null });
  },

  expire: () => {
    if (!get().token) return;
    void clearSession();
    usePushStore.getState().reset();
    setSessionToken(null);
    set({ token: null, account: null, minSeverity: null, error: SESSION_EXPIRED_TEXT });
  },

  expireIfRevoked: async () => {
    const token = get().token;
    if (!token) return;
    if ((await meRequest(token)) === 'invalid') get().expire();
  },

  clearError: () => set({ error: null }),
}));

onSessionInvalid(() => useAuthStore.getState().expire());
