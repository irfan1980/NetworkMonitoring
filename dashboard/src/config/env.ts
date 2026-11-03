import { Platform } from 'react-native';

const EXPLICIT_WS_URL = process.env.EXPO_PUBLIC_RELAY_WS_URL;

function sameOriginWsUrl(): string | null {
  if (Platform.OS !== 'web') return null;
  if (typeof window === 'undefined' || !window.location?.host) return null;
  return `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}/ws`;
}

const FALLBACK_WS_URL = 'ws://localhost:8085/ws';

const SAME_ORIGIN_WS_URL = EXPLICIT_WS_URL ? null : sameOriginWsUrl();

export const RELAY_WS_URL = EXPLICIT_WS_URL || SAME_ORIGIN_WS_URL || FALLBACK_WS_URL;

export const RELAY_WS_URL_IS_FALLBACK = !EXPLICIT_WS_URL;

// Web same-origin is the intended production path; only warn when the URL is a real guess.
if (!EXPLICIT_WS_URL && !SAME_ORIGIN_WS_URL) {
  console.warn(
    `[config] EXPO_PUBLIC_RELAY_WS_URL is not set -- falling back to ${RELAY_WS_URL}. ` +
      'Set it in .env/.env.local (or pass it on the export command) to point at the relay explicitly.'
  );
}

export const RELAY_HTTP_BASE = RELAY_WS_URL.replace(/^ws/, 'http').replace(/\/ws$/, '');

export const RELAY_WS_BASE = RELAY_WS_URL.replace(/\/ws$/, '');

export function machineWsUrl(machineId: number): string {
  return `${RELAY_WS_BASE}/ws/machine/${machineId}`;
}

const FIXTURE_ENV = process.env.EXPO_PUBLIC_FIXTURE;

export const FORCE_FIXTURE = FIXTURE_ENV === '1' || FIXTURE_ENV === '3';

export const FIXTURE_VARIANT: '3' | '4' = FIXTURE_ENV === '3' ? '3' : '4';

