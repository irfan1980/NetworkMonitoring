export type PushKind = 'first' | 'repeat';

export const SEVERITIES = [1, 2, 3, 4, 5] as const;
export const KINDS: PushKind[] = ['first', 'repeat'];

export interface PushSoundRef {
  severity: number;
  kind: PushKind;
}

export const soundId = (r: PushSoundRef) => `sev${r.severity}_${r.kind}`;
export const DEFAULT_CHANNEL_ID = 'alerts';
export const channelIdFor = (r: PushSoundRef) => soundId(r);
export const soundFileFor = (r: PushSoundRef) => `${soundId(r)}.wav`;

export const FALLBACK_SOUND: PushSoundRef = { severity: 3, kind: 'first' };

export function parsePushSound(data: unknown): PushSoundRef | null {
  const d = (data ?? {}) as Record<string, unknown>;
  const fromId = typeof d.sound_id === 'string' ? /^sev([1-5])_(first|repeat)(?:\.wav)?$/.exec(d.sound_id) : null;
  if (fromId) return { severity: Number(fromId[1]), kind: fromId[2] as PushKind };
  const severity = Number(d.severity);
  if (Number.isInteger(severity) && severity >= 1 && severity <= 5 && (d.kind === 'first' || d.kind === 'repeat')) {
    return { severity, kind: d.kind };
  }
  return null;
}
