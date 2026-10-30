import { getSessionToken, notifySessionInvalid } from './session';

export class RelayUnauthorizedError extends Error {
  constructor() {
    super('Session expired');
    this.name = 'RelayUnauthorizedError';
  }
}

export function withSessionToken(wsUrl: string): string {
  const hashIndex = wsUrl.indexOf('#');
  const base = hashIndex === -1 ? wsUrl : wsUrl.slice(0, hashIndex);
  const hash = hashIndex === -1 ? '' : wsUrl.slice(hashIndex);
  const sep = base.includes('?') ? '&' : '?';
  return `${base}${sep}token=${encodeURIComponent(getSessionToken() ?? '')}${hash}`;
}

export async function relayFetch(url: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const token = getSessionToken();
  if (token) headers.set('authorization', `Bearer ${token}`);

  const res = await fetch(url, { ...init, headers });

  if (res.status === 401) {
    let detail: unknown;
    try {
      detail = (await res.clone().json())?.detail;
    } catch {}
    // A rejected write PIN is not a session failure.
    if (detail === 'invalid_pin') return res;
    if (detail === 'session_invalid' || !token) {
      notifySessionInvalid();
      throw new RelayUnauthorizedError();
    }
  }

  return res;
}
