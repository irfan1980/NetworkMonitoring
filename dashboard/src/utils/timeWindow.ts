import type { AccountWindow } from '../types/downtime';

export const DAY_LABELS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

const HHMM = /^([01]\d|2[0-3]):([0-5]\d)$/;

export function deviceTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

export function isValidTimezone(tz: string): boolean {
  if (!tz) return false;
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz });
    return true;
  } catch {
    return false;
  }
}

export function validateWindow(w: AccountWindow): string | null {
  if (!isValidTimezone(w.timezone)) return 'Timezone must be an IANA name such as America/Chicago.';
  if (w.days.length === 0) return 'Pick at least one day.';
  if (!HHMM.test(w.start) || !HHMM.test(w.end)) return 'Times must be HH:MM (24 hour).';
  return null;
}

export function crossesMidnight(w: Pick<AccountWindow, 'start' | 'end'>): boolean {
  return w.end <= w.start;
}

export function describeDays(days: number[]): string {
  const sorted = [...new Set(days)].sort((a, b) => a - b);
  if (sorted.length === 7) return 'Every day';
  if (sorted.length === 5 && sorted.every((d, i) => d === i)) return 'Mon-Fri';
  if (sorted.length === 2 && sorted[0] === 5 && sorted[1] === 6) return 'Sat-Sun';
  return sorted.map((d) => DAY_LABELS[d]).join(', ');
}

export function describeWindow(w: AccountWindow): string {
  return `${describeDays(w.days)} ${w.start}-${w.end}${crossesMidnight(w) ? ' (+1 day)' : ''} ${w.timezone}`;
}

function localParts(now: Date, timezone: string): { day: number; minutes: number } | null {
  try {
    const parts = new Intl.DateTimeFormat('en-US', {
      timeZone: timezone,
      weekday: 'short',
      hour: '2-digit',
      minute: '2-digit',
      hourCycle: 'h23',
    }).formatToParts(now);
    const get = (t: string) => parts.find((p) => p.type === t)?.value ?? '';
    const day = DAY_LABELS.indexOf(get('weekday'));
    if (day < 0) return null;
    return { day, minutes: Number(get('hour')) * 60 + Number(get('minute')) };
  } catch {
    return null;
  }
}

const toMinutes = (hhmm: string) => Number(hhmm.slice(0, 2)) * 60 + Number(hhmm.slice(3, 5));

export function isWindowActive(w: AccountWindow, now: Date = new Date()): boolean {
  const local = localParts(now, w.timezone);
  if (!local || !HHMM.test(w.start) || !HHMM.test(w.end)) return false;
  const start = toMinutes(w.start);
  const end = toMinutes(w.end);
  if (end > start) return w.days.includes(local.day) && local.minutes >= start && local.minutes < end;
  if (local.minutes >= start && w.days.includes(local.day)) return true;
  return local.minutes < end && w.days.includes((local.day + 6) % 7);
}
