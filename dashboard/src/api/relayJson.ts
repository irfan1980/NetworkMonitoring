import { RELAY_HTTP_BASE } from '../config/env';
import { relayFetch, RelayUnauthorizedError } from './relayHttp';

export class RelayApiError extends Error {
  status: number;
  code: string;
  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = 'RelayApiError';
    this.status = status;
    this.code = code;
  }
}

export async function relayJson<T>(method: 'GET' | 'POST' | 'PUT' | 'DELETE', path: string, body?: unknown): Promise<T> {
  let res: Response;
  try {
    res = await relayFetch(`${RELAY_HTTP_BASE}${path}`, {
      method,
      headers: body !== undefined ? { 'content-type': 'application/json' } : undefined,
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    if (e instanceof RelayUnauthorizedError) throw e;
    throw new RelayApiError(0, 'network', 'Could not reach the relay.');
  }
  let parsed: any = null;
  try {
    parsed = await res.json();
  } catch {}
  if (!res.ok) {
    const detail = parsed?.detail;
    const code = typeof parsed?.error === 'string' ? parsed.error : typeof detail === 'string' ? detail : `http_${res.status}`;
    if (res.status === 404 && (detail === undefined || detail === 'Not Found')) {
      throw new RelayApiError(404, 'relay_outdated', 'The relay does not support this yet. Restart it with the latest version.');
    }
    const message = typeof parsed?.message === 'string' ? parsed.message : typeof detail?.message === 'string' ? detail.message : `Relay refused the request (${code}).`;
    throw new RelayApiError(res.status, code, message);
  }
  return parsed as T;
}
