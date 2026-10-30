import type { MachineInfo, ScheduleSummary } from '../types/relay';
import type {
  CheckScriptResult,
  PreviewResult,
  RunsPage,
  RunStartResult,
  ScheduleDetail,
  ScheduleRun,
  Trigger,
} from '../types/schedules';

const NOW = Date.now();
const iso = (offsetMs: number) => new Date(NOW + offsetMs).toISOString().replace(/\.\d{3}Z$/, 'Z');
const MIN = 60_000;
const HOUR = 3_600_000;

function machine(id: number, name: string, state: MachineInfo['scheduler_state'], reason: string | null, status: MachineInfo['status'] = 'online'): MachineInfo {
  return {
    machine_id: id,
    machine_name: name,
    machine_type: 'windows',
    machine_os: 'Windows Server 2022',
    machine_ip: `10.0.0.${id}`,
    status,
    last_probe_ts: iso(-MIN),
    last_active_ts: iso(-MIN),
    ping: { rtt_ms: 4 },
    node_count: 0,
    machine_scheduler: state !== 'off',
    scheduler_state: state,
    scheduler_reason: reason,
  };
}

export const FIXTURE_SCHEDULER_MACHINES: MachineInfo[] = [
  machine(1, 'db-host-a', 'up', null),
  machine(2, 'db-host-b', 'auth_rejected', 'scheduler auth rejected: check clock / machine IP'),
  machine(3, 'db-host-c', 'down', 'machine offline', 'offline'),
  machine(4, 'plain-host', 'off', null),
];

export const FIXTURE_SCHEDULES: ScheduleSummary[] = [
  {
    id: 1,
    name: 'MySQL nightly start',
    machine_id: 1,
    script: 'D:\\jobs\\mysql_start.cmd',
    trigger_summary: 'daily at 02:00 UTC',
    next_run: iso(5 * HOUR),
    last_run: iso(-19 * HOUR),
    last_outcome: 'success',
    consecutive_failures: 0,
    fail_threshold: 3,
    state: 'green',
    running: false,
  },
  {
    id: 2,
    name: 'Postgres vacuum',
    machine_id: 1,
    script: 'D:\\jobs\\pg_vacuum.cmd',
    trigger_summary: 'every 15 minutes',
    next_run: iso(7 * MIN),
    last_run: iso(-8 * MIN),
    last_outcome: 'failed',
    consecutive_failures: 4,
    fail_threshold: 3,
    state: 'red',
    running: false,
    reason: '4 failures in a row (threshold 3)',
  },
  {
    id: 3,
    name: 'Log rotate',
    machine_id: 2,
    script: 'D:\\jobs\\rotate.cmd',
    trigger_summary: 'weekly on Mon, Fri at 09:00 UTC',
    next_run: null,
    last_run: null,
    last_outcome: null,
    consecutive_failures: 0,
    fail_threshold: 3,
    state: 'grey',
    running: false,
    reason: 'scheduler auth rejected: check clock / machine IP',
  },
  {
    id: 4,
    name: 'Cleanup after start',
    machine_id: 1,
    script: 'D:\\jobs\\cleanup.cmd',
    trigger_summary: 'after #1 succeeds',
    next_run: null,
    last_run: iso(-19 * HOUR),
    last_outcome: 'success',
    consecutive_failures: 0,
    fail_threshold: 3,
    state: 'green',
    running: true,
  },
  {
    id: 5,
    name: 'Offline box job',
    machine_id: 3,
    script: 'D:\\jobs\\x.cmd',
    trigger_summary: 'monthly on day -1 at 23:30 UTC',
    next_run: null,
    last_run: iso(-30 * HOUR),
    last_outcome: 'missed',
    consecutive_failures: 0,
    fail_threshold: 3,
    state: 'grey',
    running: false,
    reason: 'machine offline',
  },
];

const TRIGGERS: Record<number, Trigger> = {
  1: { kind: 'daily', time: '02:00' },
  2: { kind: 'interval', every_seconds: 900, start_at: iso(-2 * HOUR) },
  3: { kind: 'weekly', time: '09:00', days_of_week: [1, 5] },
  4: { kind: 'event', event: 'after_schedule', after_schedule_id: 1, on: 'success' },
  5: { kind: 'monthly', time: '23:30', day_of_month: -1 },
};

export function buildFixtureDetail(id: number): ScheduleDetail | null {
  const row = FIXTURE_SCHEDULES.find((s) => s.id === id);
  if (!row) return null;
  return {
    ...row,
    args: id === 2 ? ['--full', '--analyze'] : [],
    enabled: id !== 3,
    trigger: TRIGGERS[id],
    controls: { no_overlap: true, timeout_s: id === 2 ? 600 : null, retries: null, retry_delay_s: null, active_from: null, active_until: null, run_if_missed: null },
    stored_fail_threshold: null,
    effective: { timeout_s: 300, retries: 1, retry_delay_s: 30, run_if_missed: false, fail_threshold: 3 },
  };
}

export function buildFixtureRuns(id: number, before: number | null): RunsPage {
  const total = 60;
  const start = before ?? total + 1;
  const runs: ScheduleRun[] = [];
  for (let seq = start - 1; seq >= 1 && runs.length < 50; seq--) {
    const failed = id === 2 && (seq === total || seq === total - 1 || seq % 7 === 0);
    const special = seq % 11 === 0 ? 'missed' : seq % 13 === 0 ? 'skipped_locked' : null;
    const outcome = special ?? (failed ? (seq % 2 ? 'failed' : 'timeout') : 'success');
    const startedMs = NOW - (total - seq + 1) * 15 * MIN;
    runs.push({
      seq,
      run_id: `fx-${id}-${seq}`,
      trigger: special === 'missed' ? 'missed_catchup' : seq % 17 === 0 ? 'manual' : id === 4 ? 'event' : 'scheduled',
      started_at: new Date(startedMs).toISOString().replace(/\.\d{3}Z$/, 'Z'),
      ended_at: special ? null : new Date(startedMs + 4300).toISOString().replace(/\.\d{3}Z$/, 'Z'),
      attempts: failed ? 2 : 1,
      exit_code: special ? null : failed ? (outcome === 'timeout' ? null : 1) : 0,
      outcome,
      reason: failed ? 'script exited with code 1' : special === 'missed' ? 'machine offline at fire time' : special ? 'previous run still active' : null,
      stdout_tail: failed ? 'starting vacuum...\nanalyzing public.orders' : 'ok',
      stderr_tail: failed ? 'ERROR: could not obtain lock on relation "orders"' : '',
    });
  }
  const last = runs[runs.length - 1];
  return { runs, next_before: last && last.seq > 1 ? last.seq : null };
}

export function buildFixturePreview(trigger: Trigger): PreviewResult {
  const next: string[] = [];
  const step = trigger.kind === 'interval' ? (trigger.every_seconds ?? 60) * 1000 : trigger.kind === 'cron' ? 5 * MIN : 24 * HOUR;
  for (let i = 1; i <= 5; i++) next.push(iso(i * step));
  return { next, summary: `${trigger.kind} (fixture preview)` };
}

export function buildFixtureCheck(script: string): CheckScriptResult {
  const ok = !script.toLowerCase().includes('missing');
  return { exists: ok, is_file: ok };
}

export function buildFixtureRunStart(id: number): RunStartResult {
  if (id === 4) return { run_id: '', status: 'skipped_locked', reason: 'already running' };
  if (id === 5) return { run_id: '', status: 'missed', reason: 'machine offline' };
  return { run_id: `fx-manual-${Date.now()}`, status: 'started' };
}
