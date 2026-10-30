import { colors } from '../theme/colors';
import { ScheduleApiError } from '../services/schedules';
import type { MachineInfo, ScheduleHealth, ScheduleSummary } from '../types/relay';
import { formatMachineLabel } from './machineLabel';

export function healthColor(state: ScheduleHealth): string {
  if (state === 'green') return colors.good;
  if (state === 'red') return colors.bad;
  return colors.unknown;
}

const FAILURE_OUTCOMES = new Set(['failed', 'timeout', 'script_not_found', 'lost']);

export function outcomeColor(outcome: string | null | undefined): string {
  if (outcome === 'success') return colors.good;
  if (outcome && FAILURE_OUTCOMES.has(outcome)) return colors.bad;
  return colors.unknown;
}

export function outcomeLabel(outcome: string | null | undefined): string {
  if (!outcome) return 'never run';
  return outcome.replace(/_/g, ' ');
}

const STATE_ORDER: Record<ScheduleHealth, number> = { red: 0, grey: 1, green: 2 };

export function sortSchedules(list: ScheduleSummary[]): ScheduleSummary[] {
  return [...list].sort((a, b) => STATE_ORDER[a.state] - STATE_ORDER[b.state] || a.id - b.id);
}

export function machineName(machines: Record<number, MachineInfo>, machineId: number): string {
  const m = machines[machineId];
  return m ? formatMachineLabel(m.machine_name, m.machine_label_id ?? m.machine_id) : `Machine ${machineId}`;
}

export function schedulerStateColor(state: MachineInfo['scheduler_state']): string {
  return state === 'up' ? colors.good : colors.unknown;
}

export function schedulerStateLabel(state: MachineInfo['scheduler_state']): string {
  switch (state) {
    case 'up':
      return 'Scheduler up';
    case 'down':
      return 'Scheduler down';
    case 'auth_rejected':
      return 'Scheduler auth rejected';
    default:
      return 'Scheduler off';
  }
}

export function describeScheduleError(e: unknown): string {
  if (e instanceof ScheduleApiError) {
    if (e.status === 429) return 'Too many attempts, wait a bit and try again.';
    if (e.code === 'running') return 'Running, try again after it finishes.';
    if (e.code === 'scheduler_disabled') return e.message || 'The scheduler is disabled on the relay.';
    if (e.code === 'endpoint_unavailable') return 'The machine\'s scheduler endpoint is unavailable.';
    if (e.code === 'auth_rejected') return 'Scheduler auth rejected: check clock / machine IP.';
    if (e.code === 'not_running') return 'It is not running any more.';
    if (e.status === 404) return 'Schedule not found.';
    return e.message;
  }
  return e instanceof Error ? e.message : 'Something went wrong.';
}
