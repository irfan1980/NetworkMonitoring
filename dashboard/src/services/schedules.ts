import { FORCE_FIXTURE, RELAY_HTTP_BASE } from '../config/env';
import { relayFetch } from '../api/relayHttp';
import {
  buildFixtureCheck,
  buildFixtureDetail,
  buildFixturePreview,
  buildFixtureRuns,
  buildFixtureRunStart,
} from '../fixtures/schedules';
import type {
  CheckScriptResult,
  FieldError,
  PreviewResult,
  RunsPage,
  RunStartResult,
  StopRunResult,
  ScheduleDetail,
  ScheduleInput,
  ScheduleRun,
  Trigger,
} from '../types/schedules';

export class ScheduleApiError extends Error {
  status: number;
  code: string | undefined;
  fieldErrors: FieldError[];
  constructor(status: number, code: string | undefined, message: string, fieldErrors: FieldError[] = []) {
    super(message);
    this.name = 'ScheduleApiError';
    this.status = status;
    this.code = code;
    this.fieldErrors = fieldErrors;
  }
}

const BASE = `${RELAY_HTTP_BASE}/api/schedules`;

async function parseError(res: Response): Promise<ScheduleApiError> {
  let body: any = null;
  try {
    body = await res.json();
  } catch {
  }
  const detail = body?.detail;
  const code: string | undefined =
    typeof body?.error === 'string' ? body.error : typeof detail === 'string' ? detail : typeof detail?.error === 'string' ? detail.error : undefined;
  const message: string =
    typeof body?.message === 'string' ? body.message : typeof detail?.message === 'string' ? detail.message : code ?? `Request failed (${res.status})`;
  const fieldErrors: FieldError[] = Array.isArray(body?.errors) ? body.errors : [];
  return new ScheduleApiError(res.status, code, message, fieldErrors);
}

async function request<T>(method: string, url: string, opts: { body?: unknown; pin?: string } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (opts.body !== undefined) headers['content-type'] = 'application/json';
  if (opts.pin) headers['X-Ack-Pin'] = opts.pin;
  const res = await relayFetch(url, { method, headers, body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined });
  if (!res.ok) throw await parseError(res);
  return (await res.json()) as T;
}

function fixtureGate(pin?: string) {
  if (pin === '0000') throw new ScheduleApiError(401, 'invalid_pin', 'invalid_pin');
  if (pin === '9999') throw new ScheduleApiError(429, 'rate_limited', 'rate_limited');
}

export async function getSchedule(id: number): Promise<ScheduleDetail> {
  if (FORCE_FIXTURE) {
    const d = buildFixtureDetail(id);
    if (!d) throw new ScheduleApiError(404, 'not_found', 'schedule not found');
    return d;
  }
  return request<ScheduleDetail>('GET', `${BASE}/${id}`);
}

export async function getScheduleRuns(id: number, before: number | null, limit = 50): Promise<RunsPage> {
  if (FORCE_FIXTURE) return buildFixtureRuns(id, before);
  const qs = `limit=${limit}${before != null ? `&before=${before}` : ''}`;
  return request<RunsPage>('GET', `${BASE}/${id}/runs?${qs}`);
}

export async function previewTrigger(trigger: Trigger): Promise<PreviewResult> {
  if (FORCE_FIXTURE) return buildFixturePreview(trigger);
  return request<PreviewResult>('POST', `${BASE}/preview`, { body: { trigger } });
}

export async function createSchedule(input: ScheduleInput, pin: string): Promise<ScheduleDetail> {
  if (FORCE_FIXTURE) {
    fixtureGate(pin);
    return { ...buildFixtureDetail(1)!, ...input, id: 99 } as unknown as ScheduleDetail;
  }
  return request<ScheduleDetail>('POST', BASE, { body: input, pin });
}

export async function updateSchedule(id: number, input: ScheduleInput, pin: string): Promise<ScheduleDetail> {
  if (FORCE_FIXTURE) {
    fixtureGate(pin);
    return { ...buildFixtureDetail(id)!, ...input } as unknown as ScheduleDetail;
  }
  return request<ScheduleDetail>('PUT', `${BASE}/${id}`, { body: input, pin });
}

export async function deleteSchedule(id: number, pin: string): Promise<{ deleted: boolean; id: number }> {
  if (FORCE_FIXTURE) {
    fixtureGate(pin);
    return { deleted: true, id };
  }
  return request('DELETE', `${BASE}/${id}`, { pin });
}

export async function setScheduleEnabled(id: number, enabled: boolean, pin: string): Promise<ScheduleDetail> {
  if (FORCE_FIXTURE) {
    fixtureGate(pin);
    return { ...buildFixtureDetail(id)!, enabled };
  }
  return request<ScheduleDetail>('POST', `${BASE}/${id}/${enabled ? 'enable' : 'disable'}`, { pin });
}

export async function checkScript(machineId: number, script: string, pin: string): Promise<CheckScriptResult> {
  if (FORCE_FIXTURE) {
    fixtureGate(pin);
    return buildFixtureCheck(script);
  }
  return request<CheckScriptResult>('POST', `${BASE}/check-script`, { body: { machine_id: machineId, script }, pin });
}

export async function runSchedule(id: number, pin: string): Promise<RunStartResult> {
  if (FORCE_FIXTURE) {
    fixtureGate(pin);
    return buildFixtureRunStart(id);
  }
  return request<RunStartResult>('POST', `${BASE}/${id}/run`, { pin });
}

export async function findRun(id: number, runId: string): Promise<ScheduleRun | null> {
  if (FORCE_FIXTURE) {
    const now = new Date().toISOString().replace(/\.\d{3}Z$/, 'Z');
    return { seq: 0, run_id: runId, trigger: 'manual', started_at: now, ended_at: now, attempts: 1, exit_code: 0, outcome: 'success', reason: null, stdout_tail: null, stderr_tail: null };
  }
  const page = await getScheduleRuns(id, null, 10);
  return page.runs.find((r) => r.run_id === runId) ?? null;
}

export async function stopSchedule(id: number, pin: string): Promise<StopRunResult> {
  if (FORCE_FIXTURE) {
    fixtureGate(pin);
    return { run_id: `fx-stop-${id}`, status: 'cancelled' };
  }
  return request<StopRunResult>('POST', `${BASE}/${id}/stop`, { pin });
}
