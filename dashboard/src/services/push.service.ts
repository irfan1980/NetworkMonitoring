import { RELAY_HTTP_BASE } from '../config/env';
import { relayFetch, RelayUnauthorizedError } from '../api/relayHttp';
import type { PushPlatform, PushRegistration, PushStatus } from '../types/push';

export class PushApiError extends Error {
  code: string;
  constructor(code: string, message?: string) {
    super(message ?? code);
    this.name = 'PushApiError';
    this.code = code;
  }
}

const ERROR_TEXT: Record<string, string> = {
  unknown_account_secret: 'That account secret is not recognised.',
  invalid_account_secret: 'That account secret is not recognised.',
  unknown_secret: 'That account secret is not recognised.',
  rate_limited: 'Too many attempts. Wait a minute and try again.',
  unknown_alert: 'That alert is no longer active.',
  not_found: 'Not found on the relay.',
};

async function readBody(res: Response): Promise<Record<string, unknown>> {
  try {
    const body = await res.json();
    return body && typeof body === 'object' ? (body as Record<string, unknown>) : {};
  } catch {
    return {};
  }
}

async function post(path: string, payload: unknown): Promise<Record<string, unknown>> {
  let res: Response;
  try {
    res = await relayFetch(`${RELAY_HTTP_BASE}${path}`, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch (e) {
    if (e instanceof RelayUnauthorizedError) throw e;
    throw new PushApiError('network', 'Could not reach the relay.');
  }
  const body = await readBody(res);
  if (res.status === 404 && (body.detail === undefined || body.detail === 'Not Found')) {
    throw new PushApiError('relay_outdated', 'Relay needs restarting to enable login/push.');
  }
  if (!res.ok) {
    const code = String(body.detail ?? body.error ?? `http_${res.status}`);
    const message = typeof body.message === 'string' ? body.message : null;
    throw new PushApiError(code, ERROR_TEXT[code] ?? message ?? `Relay refused the request (${code}).`);
  }
  return body;
}

function toNumber(v: unknown): number | null {
  if (typeof v === 'number') return v;
  if (typeof v === 'string' && v !== '' && !Number.isNaN(Number(v))) return Number(v);
  return null;
}

function toRegistration(body: Record<string, unknown>): PushRegistration {
  const account = body.account ?? body.account_name ?? body.name;
  return {
    account: typeof account === 'string' ? account : 'account',
    minSeverity: toNumber(body.min_severity ?? body.minSeverity),
    confirmationSent: typeof body.confirmation_sent === 'boolean' ? body.confirmation_sent : null,
    confirmationError: typeof body.confirmation_error === 'string' ? body.confirmation_error : null,
  };
}

export async function registerPush(args: {
  token: string;
  platform: PushPlatform;
  deviceName: string;
}): Promise<PushRegistration> {
  const body = await post('/api/push/register', {
    token: args.token,
    platform: args.platform,
    device_name: args.deviceName,
  });
  return toRegistration(body);
}

export async function unregisterPush(token: string): Promise<void> {
  await post('/api/push/unregister', { token });
}

export async function fetchPushStatus(token: string): Promise<PushStatus> {
  const res = await relayFetch(`${RELAY_HTTP_BASE}/api/push/status?token=${encodeURIComponent(token)}`);
  if (!res.ok) throw new PushApiError(`http_${res.status}`, `Relay refused the request (${res.status}).`);
  const body = await readBody(res);
  const reg = toRegistration(body);
  const registered =
    body.registered === undefined ? typeof (body.account ?? body.account_name ?? body.name) === 'string' : body.registered === true;
  return {
    registered,
    account: registered ? reg.account : null,
    minSeverity: registered ? reg.minSeverity : null,
    pushEnabled: body.push_enabled !== false,
  };
}

export async function mutePush(alertId: string): Promise<void> {
  await post('/api/push/mute', { alertId });
}

export async function unmutePush(alertId: string): Promise<void> {
  await post('/api/push/unmute', { alertId });
}

export async function fetchPushMutes(token: string): Promise<string[]> {
  const res = await relayFetch(`${RELAY_HTTP_BASE}/api/push/mutes?token=${encodeURIComponent(token)}`);
  if (!res.ok) throw new PushApiError(`http_${res.status}`, `Relay refused the request (${res.status}).`);
  const body = await readBody(res);
  return Array.isArray(body.muted) ? body.muted.filter((x): x is string => typeof x === 'string') : [];
}
