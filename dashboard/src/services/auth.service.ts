import { RELAY_HTTP_BASE } from '../config/env';
import type { PushPlatform } from '../types/push';

export interface LoginResult {
  token: string;
  account: string;
  minSeverity: number | null;
  expiresAt: string | null;
}

export class LoginError extends Error {
  code: string;
  constructor(code: string, message: string) {
    super(message);
    this.name = 'LoginError';
    this.code = code;
  }
}

const TEXT: Record<string, string> = {
  invalid_account_secret: 'That account secret is not recognised.',
  rate_limited: 'Too many attempts. Wait a minute and try again.',
};

function firstDetailMsg(detail: unknown): string | null {
  if (typeof detail === 'string') return detail;
  const first = Array.isArray(detail) ? detail[0] : null;
  return first && typeof first.msg === 'string' ? first.msg : null;
}

function errorCode(body: Record<string, unknown>, status: number): string {
  if (typeof body.error === 'string') return body.error;
  return typeof body.detail === 'string' ? body.detail : `http_${status}`;
}

function errorMessage(body: Record<string, unknown>, code: string): string {
  if (typeof body.message === 'string') return body.message;
  return firstDetailMsg(body.detail) ?? `Sign-in failed (${code}).`;
}

export async function loginRequest(args: {
  accountSecret: string;
  deviceName: string;
  deviceId: string | null;
  platform: PushPlatform;
}): Promise<LoginResult> {
  let res: Response;
  try {
    res = await fetch(`${RELAY_HTTP_BASE}/api/auth/login`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        account_secret: args.accountSecret,
        device_name: args.deviceName,
        ...(args.deviceId ? { device_id: args.deviceId } : {}),
        platform: args.platform,
      }),
    });
  } catch {
    throw new LoginError('network', 'Could not reach the relay.');
  }
  let body: Record<string, unknown> = {};
  try {
    body = await res.json();
  } catch {}
  if (res.status === 404 && (body.detail === undefined || body.detail === 'Not Found')) {
    throw new LoginError('relay_outdated', 'Relay needs restarting to enable login/push.');
  }
  if (!res.ok) {
    const code = errorCode(body, res.status);
    throw new LoginError(code, TEXT[code] ?? errorMessage(body, code));
  }
  if (typeof body.token !== 'string') throw new LoginError('bad_response', 'Unexpected response from the relay.');
  return {
    token: body.token,
    account: typeof body.account === 'string' ? body.account : 'account',
    minSeverity: typeof body.min_severity === 'number' ? body.min_severity : null,
    expiresAt: typeof body.expires_at === 'string' ? body.expires_at : null,
  };
}

export async function logoutRequest(token: string): Promise<void> {
  await fetch(`${RELAY_HTTP_BASE}/api/auth/logout`, {
    method: 'POST',
    headers: { authorization: `Bearer ${token}` },
  });
}

export async function meRequest(token: string): Promise<'ok' | 'invalid' | 'unknown'> {
  try {
    const res = await fetch(`${RELAY_HTTP_BASE}/api/auth/me`, { headers: { authorization: `Bearer ${token}` } });
    if (res.status === 401) {
      // Only the relay's explicit verdict ends a session; any other 401 body is treated as transient.
      const body = await res.json().catch(() => null);
      return body?.detail === 'session_invalid' ? 'invalid' : 'unknown';
    }
    return res.ok ? 'ok' : 'unknown';
  } catch {
    return 'unknown';
  }
}
