import type { ScheduleSummary } from './relay';

export type TriggerKind = 'cron' | 'once' | 'interval' | 'daily' | 'weekly' | 'monthly' | 'event';
export type EventKind = 'machine_online' | 'relay_start' | 'after_schedule';
export type AfterOn = 'success' | 'failure' | 'any';

export interface Trigger {
  kind: TriggerKind;
  cron?: string;
  at?: string;
  every_seconds?: number;
  start_at?: string;
  time?: string;
  days_of_week?: number[];
  day_of_month?: number;
  event?: EventKind;
  after_schedule_id?: number;
  on?: AfterOn;
}

export interface ScheduleControls {
  timeout_s?: number | null;
  retries?: number | null;
  retry_delay_s?: number | null;
  no_overlap: boolean;
  active_from?: string | null;
  active_until?: string | null;
  run_if_missed?: boolean | null;
}

export interface EffectiveControls {
  timeout_s: number;
  retries: number;
  retry_delay_s: number;
  run_if_missed: boolean;
  fail_threshold: number;
}

export type EffectiveSource = 'schedule' | 'machine' | 'default';

export interface ScheduleDetail extends ScheduleSummary {
  args: string[];
  enabled: boolean;
  trigger: Trigger;
  controls: ScheduleControls;
  stored_fail_threshold: number | null;
  effective: EffectiveControls;
  timeout_display?: string;
  effective_source?: Partial<Record<keyof EffectiveControls, EffectiveSource>>;
}

export interface ScheduleInput {
  name: string;
  machine_id: number;
  script: string;
  args: string[];
  enabled: boolean;
  trigger: Trigger;
  controls: ScheduleControls;
  fail_threshold: number | null;
}

export type RunTriggerSource = 'scheduled' | 'manual' | 'event' | 'missed_catchup';

export interface ScheduleRun {
  seq: number;
  run_id: string;
  trigger: RunTriggerSource;
  started_at: string;
  ended_at: string | null;
  attempts: number;
  exit_code: number | null;
  outcome: string;
  reason: string | null;
  stdout_tail: string | null;
  stderr_tail: string | null;
  script_path?: string | null;
  interpreter?: string | null;
}

export interface RunsPage {
  runs: ScheduleRun[];
  next_before: number | null;
}

export interface PreviewResult {
  next: string[];
  summary: string;
}

export interface FieldError {
  field: string;
  message: string;
}

export interface StopRunResult {
  run_id: string;
  status: 'cancelled' | 'stopping';
}

export interface CheckScriptResult {
  exists: boolean;
  is_file: boolean;
  resolved?: string;
  error?: string;
}

export interface RunStartResult {
  run_id: string;
  status: 'started' | 'skipped_locked' | 'missed';
  reason?: string;
}
