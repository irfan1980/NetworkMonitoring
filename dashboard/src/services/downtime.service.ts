import { relayJson } from '../api/relayJson';
import type { AccountWindow, DowntimeInput, DowntimeWindow } from '../types/downtime';

export async function listDowntime(): Promise<DowntimeWindow[]> {
  const body = await relayJson<{ windows?: DowntimeWindow[] }>('GET', '/api/downtime');
  return Array.isArray(body.windows) ? body.windows : [];
}

export const createDowntime = (input: DowntimeInput) => relayJson<DowntimeWindow>('POST', '/api/downtime', input);

export const updateDowntime = (id: DowntimeWindow['id'], input: DowntimeInput) =>
  relayJson<DowntimeWindow>('PUT', `/api/downtime/${encodeURIComponent(String(id))}`, input);

export const deleteDowntime = (id: DowntimeWindow['id']) =>
  relayJson<unknown>('DELETE', `/api/downtime/${encodeURIComponent(String(id))}`);

export async function getPushWindow(): Promise<AccountWindow | null> {
  const body = await relayJson<{ window?: AccountWindow | null }>('GET', '/api/push/window');
  return body.window ?? null;
}

export const setPushWindow = (window: AccountWindow | null) => relayJson<unknown>('PUT', '/api/push/window', window ?? { window: null });
