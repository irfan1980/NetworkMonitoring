import { FORCE_FIXTURE, RELAY_HTTP_BASE } from '../config/env';
import { relayFetch } from '../api/relayHttp';
import { buildFixtureBuses, buildFixturePairEntities, buildFixturePlatformEntities } from '../fixtures/diagnostics';
import type { BusesResponse, EntityDiagnostic, LastError, PairEntitiesResponse, PlatformEntitiesResponse } from '../types/diagnostics';

const str = (v: unknown): string | null => (typeof v === 'string' && v !== '' ? v : null);
const num = (v: unknown): number => (typeof v === 'number' && Number.isFinite(v) ? v : 0);

function normalizeLastError(v: unknown): LastError | null {
  if (typeof v === 'string') return v ? { at: '', message: v, messageId: '' } : null;
  if (v && typeof v === 'object') {
    const o = v as Record<string, unknown>;
    return { at: str(o.at) ?? '', message: str(o.message) ?? 'Unknown error', messageId: str(o.messageId) ?? '' };
  }
  return null;
}

// Tolerates rows from an older or differently shaped relay so one odd field cannot crash a modal.
export function normalizeEntity(raw: unknown): EntityDiagnostic {
  const o = (raw && typeof raw === 'object' ? raw : {}) as Record<string, unknown>;
  const format = str(o.format);
  return {
    entity: str(o.entity) ?? str(o.entityType) ?? str(o.name) ?? 'unknown',
    pipelineId: o.pipelineId == null ? '' : String(o.pipelineId),
    senderFunction: str(o.senderFunction),
    senderFormat: str(o.senderFormat) ?? format,
    receiverProcedure: str(o.receiverProcedure),
    receiverFormat: str(o.receiverFormat) ?? format,
    operations: Array.isArray(o.operations) ? o.operations.filter((x): x is string => typeof x === 'string') : [],
    severity: typeof o.severity === 'number' && Number.isFinite(o.severity) ? o.severity : null,
    pending: num(o.pending),
    sentInWindow: num(o.sentInWindow),
    appliedInWindow: num(o.appliedInWindow),
    errors: num(o.errors),
    lastSentAt: str(o.lastSentAt),
    lastAppliedAt: str(o.lastAppliedAt) ?? str(o.lastUsed),
    lastError: normalizeLastError(o.lastError),
    queued: num(o.queued),
  };
}

const normalizeList = (v: unknown): EntityDiagnostic[] => (Array.isArray(v) ? v.map(normalizeEntity) : []);

export async function fetchPairEntities(from: string, to: string): Promise<PairEntitiesResponse> {
  if (FORCE_FIXTURE) return buildFixturePairEntities(from, to);
  const res = await relayFetch(`${RELAY_HTTP_BASE}/api/pairs/${encodeURIComponent(from)}/${encodeURIComponent(to)}/entities`);
  if (res.status === 503) {
    const body = await res.json().catch(() => null);
    const d = body?.detail;
    if (d?.error === 'nothing_readable') {
      const parts = [d.from, d.to]
        .map((s: { available?: boolean; detail?: string; reason?: string } | undefined) => (s && s.available === false ? s.detail ?? s.reason : null))
        .filter((t): t is string => !!t);
      throw new Error(`Both databases are unavailable${parts.length ? `: ${parts.join('; ')}` : ''}.`);
    }
  }
  if (!res.ok) throw new Error(`Failed to load pair diagnostics (${res.status})`);
  const body = (await res.json()) as PairEntitiesResponse;
  return { ...body, window: body.window ?? { seconds: 3600 }, entities: normalizeList(body.entities) };
}

export async function fetchPlatformEntities(platformId: string): Promise<PlatformEntitiesResponse> {
  if (FORCE_FIXTURE) return buildFixturePlatformEntities(platformId);
  const res = await relayFetch(`${RELAY_HTTP_BASE}/api/servers/${encodeURIComponent(platformId)}/entities`);
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    if (res.status === 503 && typeof detail === 'string') throw new Error(`This server's database is unavailable: ${detail}`);
    throw new Error(`Failed to load platform diagnostics (${res.status})`);
  }
  const body = (await res.json()) as PlatformEntitiesResponse;
  return {
    ...body,
    outgoing: (body.outgoing ?? []).map((g) => ({ ...g, entities: normalizeList(g.entities) })),
    incoming: (body.incoming ?? []).map((g) => ({ ...g, entities: normalizeList(g.entities) })),
  };
}

export async function fetchBuses(): Promise<BusesResponse> {
  if (FORCE_FIXTURE) return buildFixtureBuses();
  const res = await relayFetch(`${RELAY_HTTP_BASE}/api/buses`);
  if (!res.ok) throw new Error(`Failed to load bus list (${res.status})`);
  return (await res.json()) as BusesResponse;
}
