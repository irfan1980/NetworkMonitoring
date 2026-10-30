import type { AfterOn, EventKind, ScheduleControls, ScheduleDetail, ScheduleInput, Trigger, TriggerKind } from '../types/schedules';

export type IntervalUnit = 'seconds' | 'minutes' | 'hours';
export type MissedChoice = 'default' | 'yes' | 'no';

export interface TriggerForm {
  kind: TriggerKind;
  cron: string;
  onceAt: string;
  intervalValue: string;
  intervalUnit: IntervalUnit;
  startAt: string | null;
  time: string;
  days: number[];
  dayOfMonth: string;
  event: EventKind;
  afterId: string;
  on: AfterOn;
}

export interface ControlsForm {
  timeout: string;
  retries: string;
  retryDelay: string;
  noOverlap: boolean;
  activeFrom: string;
  activeUntil: string;
  runIfMissed: MissedChoice;
}

export interface ScheduleForm {
  name: string;
  machineId: number | null;
  script: string;
  args: string[];
  enabled: boolean;
  trigger: TriggerForm;
  controls: ControlsForm;
  failThreshold: string;
}

const UNIT_SECONDS: Record<IntervalUnit, number> = { seconds: 1, minutes: 60, hours: 3600 };

export function defaultTriggerForm(): TriggerForm {
  return {
    kind: 'daily',
    cron: '',
    onceAt: '',
    intervalValue: '15',
    intervalUnit: 'minutes',
    startAt: null,
    time: '09:00',
    days: [1, 2, 3, 4, 5],
    dayOfMonth: '1',
    event: 'machine_online',
    afterId: '',
    on: 'success',
  };
}

export function defaultForm(machineId: number | null): ScheduleForm {
  return {
    name: '',
    machineId,
    script: '',
    args: [],
    enabled: true,
    trigger: defaultTriggerForm(),
    controls: { timeout: '', retries: '', retryDelay: '', noOverlap: true, activeFrom: '', activeUntil: '', runIfMissed: 'default' },
    failThreshold: '',
  };
}

function isoToInput(iso: string | null | undefined): string {
  if (!iso) return '';
  return iso.replace('T', ' ').replace('Z', '').replace(/:00$/, '');
}

export function inputToIso(text: string): string | undefined {
  const t = text.trim();
  if (!t) return undefined;
  const m = /^(\d{4}-\d{2}-\d{2})[ T](\d{2}:\d{2})(?::(\d{2}))?Z?$/.exec(t);
  return m ? `${m[1]}T${m[2]}:${m[3] ?? '00'}Z` : t;
}

function numOrNull(text: string): number | null {
  const t = text.trim();
  if (!t) return null;
  const n = Number(t);
  return Number.isFinite(n) ? n : NaN;
}

export function formFromDetail(d: ScheduleDetail): ScheduleForm {
  const t = d.trigger;
  let intervalValue = '15';
  let intervalUnit: IntervalUnit = 'minutes';
  if (t.every_seconds != null) {
    const s = t.every_seconds;
    if (s % 3600 === 0) {
      intervalValue = String(s / 3600);
      intervalUnit = 'hours';
    } else if (s % 60 === 0) {
      intervalValue = String(s / 60);
      intervalUnit = 'minutes';
    } else {
      intervalValue = String(s);
      intervalUnit = 'seconds';
    }
  }
  const c = d.controls;
  return {
    name: d.name,
    machineId: d.machine_id,
    script: d.script,
    args: [...d.args],
    enabled: d.enabled,
    trigger: {
      ...defaultTriggerForm(),
      kind: t.kind,
      cron: t.cron ?? '',
      onceAt: isoToInput(t.at),
      intervalValue,
      intervalUnit,
      startAt: t.start_at ?? null,
      time: t.time ?? '09:00',
      days: t.days_of_week ?? [],
      dayOfMonth: t.day_of_month != null ? String(t.day_of_month) : '1',
      event: t.event ?? 'machine_online',
      afterId: t.after_schedule_id != null ? String(t.after_schedule_id) : '',
      on: t.on ?? 'success',
    },
    controls: {
      timeout: c.timeout_s != null ? String(c.timeout_s) : '',
      retries: c.retries != null ? String(c.retries) : '',
      retryDelay: c.retry_delay_s != null ? String(c.retry_delay_s) : '',
      noOverlap: c.no_overlap,
      activeFrom: isoToInput(c.active_from),
      activeUntil: isoToInput(c.active_until),
      runIfMissed: c.run_if_missed == null ? 'default' : c.run_if_missed ? 'yes' : 'no',
    },
    failThreshold: d.stored_fail_threshold != null ? String(d.stored_fail_threshold) : '',
  };
}

// Returns null while a required field is still empty so the live preview stays quiet.
export function buildTrigger(f: TriggerForm): Trigger | null {
  switch (f.kind) {
    case 'cron':
      return f.cron.trim() ? { kind: 'cron', cron: f.cron.trim() } : null;
    case 'once': {
      const at = inputToIso(f.onceAt);
      return at ? { kind: 'once', at } : null;
    }
    case 'interval': {
      const n = numOrNull(f.intervalValue);
      if (n == null) return null;
      const t: Trigger = { kind: 'interval', every_seconds: Math.round(n * UNIT_SECONDS[f.intervalUnit]) };
      if (f.startAt) t.start_at = f.startAt;
      return t;
    }
    case 'daily':
      return f.time.trim() ? { kind: 'daily', time: f.time.trim() } : null;
    case 'weekly':
      return f.time.trim() && f.days.length ? { kind: 'weekly', time: f.time.trim(), days_of_week: [...f.days].sort() } : null;
    case 'monthly': {
      const dom = numOrNull(f.dayOfMonth);
      return f.time.trim() && dom != null ? { kind: 'monthly', time: f.time.trim(), day_of_month: dom } : null;
    }
    case 'event': {
      if (f.event !== 'after_schedule') return { kind: 'event', event: f.event };
      const id = numOrNull(f.afterId);
      return id != null ? { kind: 'event', event: 'after_schedule', after_schedule_id: id, on: f.on } : null;
    }
  }
}

export function buildInput(f: ScheduleForm): ScheduleInput | null {
  const trigger = buildTrigger(f.trigger);
  if (!trigger || f.machineId == null) return null;
  const c = f.controls;
  const controls: ScheduleControls = {
    timeout_s: numOrNull(c.timeout),
    retries: numOrNull(c.retries),
    retry_delay_s: numOrNull(c.retryDelay),
    no_overlap: c.noOverlap,
    active_from: inputToIso(c.activeFrom) ?? null,
    active_until: inputToIso(c.activeUntil) ?? null,
    run_if_missed: c.runIfMissed === 'default' ? null : c.runIfMissed === 'yes',
  };
  return {
    name: f.name.trim(),
    machine_id: f.machineId,
    script: f.script.trim(),
    args: f.args.map((a) => a.trim()).filter((a) => a.length > 0),
    enabled: f.enabled,
    trigger,
    controls,
    fail_threshold: numOrNull(f.failThreshold),
  };
}

export function fieldMessages(errors: { field: string; message: string }[], keys: string[]): string[] {
  return errors
    .filter((e) => e.field.split(/[.\[\]]+/).some((seg) => keys.includes(seg)))
    .map((e) => e.message);
}
