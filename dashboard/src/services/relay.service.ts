import { RELAY_HTTP_BASE } from '../config/env';
import { relayFetch } from '../api/relayHttp';

export type ScenarioType =
  | 'receiver_missing'
  | 'dead_letter_spike'
  | 'sender_flood'
  | 'slow_consumer'
  | 'all_healthy'
  | 'full_outage';

export async function triggerScenario(
  type: ScenarioType,
  opts: { target?: string; duration?: number } = {}
): Promise<void> {
  const params = new URLSearchParams();
  if (opts.target) params.set('target', opts.target);
  if (opts.duration) params.set('duration', String(opts.duration));
  const qs = params.toString();
  await relayFetch(`${RELAY_HTTP_BASE}/scenario/${type}${qs ? `?${qs}` : ''}`, { method: 'POST' });
}

export async function clearScenarios(): Promise<void> {
  await relayFetch(`${RELAY_HTTP_BASE}/scenario/clear`, { method: 'POST' });
}
