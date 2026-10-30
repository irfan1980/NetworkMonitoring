import React from 'react';
import { Pressable, Text, View } from 'react-native';
import { createThemedStyles } from '../../theme/ThemeContext';
import { colors } from '../../theme/colors';
import { formatUtc } from '../../utils/format';
import { type IntervalUnit, type TriggerForm } from '../../utils/scheduleForm';
import type { ScheduleSummary } from '../../types/relay';
import type { FieldError, PreviewResult, TriggerKind } from '../../types/schedules';
import { Chip, FormField, Input } from './ui';

const useStyles = createThemedStyles((c) => ({
  wrap: { gap: 12 },
  radios: { flexDirection: 'row', flexWrap: 'wrap', gap: 14 },
  radio: { flexDirection: 'row', alignItems: 'center', gap: 6 },
  radioDot: { width: 16, height: 16, borderRadius: 8, borderWidth: 2, alignItems: 'center', justifyContent: 'center' },
  radioDotInner: { width: 8, height: 8, borderRadius: 4 },
  radioText: { fontSize: 13, fontWeight: '700' },
  presets: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  presetLabel: { color: c.textMuted, fontSize: 11, fontWeight: '800', textTransform: 'uppercase', letterSpacing: 0.5 },
  row: { flexDirection: 'row', gap: 10, alignItems: 'flex-end', flexWrap: 'wrap' },
  preview: { backgroundColor: c.background, borderRadius: 8, padding: 10, gap: 4 },
  previewTitle: { color: c.textMuted, fontSize: 11, fontWeight: '800', textTransform: 'uppercase', letterSpacing: 0.5 },
  previewSummary: { color: c.textPrimary, fontSize: 13, fontWeight: '700' },
  previewLine: { color: c.textSecondary, fontSize: 12 },
}));

const KINDS: { kind: TriggerKind; label: string }[] = [
  { kind: 'cron', label: 'Cron' },
  { kind: 'once', label: 'One-time' },
  { kind: 'interval', label: 'Interval' },
  { kind: 'daily', label: 'Daily' },
  { kind: 'weekly', label: 'Weekly' },
  { kind: 'monthly', label: 'Monthly' },
  { kind: 'event', label: 'Event' },
];

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

interface Preset {
  label: string;
  apply: (f: TriggerForm) => TriggerForm;
}

const unit = (value: string, u: IntervalUnit): Preset['apply'] => (f) => ({ ...f, intervalValue: value, intervalUnit: u });

const PRESETS: Partial<Record<TriggerKind, Preset[]>> = {
  cron: [
    { label: '@hourly', apply: (f) => ({ ...f, cron: '@hourly' }) },
    { label: '@daily', apply: (f) => ({ ...f, cron: '@daily' }) },
    { label: '@weekly', apply: (f) => ({ ...f, cron: '@weekly' }) },
    { label: '@reboot', apply: (f) => ({ ...f, cron: '@reboot' }) },
    { label: 'Every 5 min', apply: (f) => ({ ...f, cron: '*/5 * * * *' }) },
    { label: 'Weekdays 09:00', apply: (f) => ({ ...f, cron: '0 9 * * 1-5' }) },
  ],
  interval: [
    { label: '1 min', apply: unit('1', 'minutes') },
    { label: '5 min', apply: unit('5', 'minutes') },
    { label: '15 min', apply: unit('15', 'minutes') },
    { label: '60 min', apply: unit('60', 'minutes') },
  ],
  daily: [
    { label: '00:00', apply: (f) => ({ ...f, time: '00:00' }) },
    { label: '09:00', apply: (f) => ({ ...f, time: '09:00' }) },
  ],
  weekly: [
    { label: 'Weekdays', apply: (f) => ({ ...f, days: [1, 2, 3, 4, 5] }) },
    { label: 'Weekend', apply: (f) => ({ ...f, days: [0, 6] }) },
    { label: 'Every day', apply: (f) => ({ ...f, days: [0, 1, 2, 3, 4, 5, 6] }) },
  ],
  monthly: [
    { label: '1st', apply: (f) => ({ ...f, dayOfMonth: '1' }) },
    { label: 'Last day', apply: (f) => ({ ...f, dayOfMonth: '-1' }) },
  ],
};

interface Props {
  value: TriggerForm;
  onChange: (next: TriggerForm) => void;
  schedules: ScheduleSummary[];
  selfId?: number;
  errors: FieldError[];
  message: string | null;
  preview: PreviewResult | null;
  previewLoading: boolean;
}

export function TriggerEditor({ value, onChange, schedules, selfId, errors, message, preview, previewLoading }: Props) {
  const styles = useStyles();
  const set = (patch: Partial<TriggerForm>) => onChange({ ...value, ...patch });
  const presets = PRESETS[value.kind];
  const errorMessages = [...errors.map((e) => e.message), ...(message ? [message] : [])];
  const invalid = errorMessages.length > 0;

  const timeInput = (
    <FormField label="Time (UTC, HH:MM)">
      <Input value={value.time} onChangeText={(time) => set({ time })} placeholder="09:00" invalid={invalid} style={{ width: 110 }} />
    </FormField>
  );

  return (
    <View style={styles.wrap}>
      <View style={styles.radios}>
        {KINDS.map((k) => {
          const active = value.kind === k.kind;
          const tint = active ? colors.accent : colors.textMuted;
          return (
            <Pressable
              key={k.kind}
              style={styles.radio}
              onPress={() => onChange(active ? value : { ...value, kind: k.kind })}
            >
              <View style={[styles.radioDot, { borderColor: tint }]}>{active ? <View style={[styles.radioDotInner, { backgroundColor: tint }]} /> : null}</View>
              <Text style={[styles.radioText, { color: active ? colors.textPrimary : colors.textSecondary }]}>{k.label}</Text>
            </Pressable>
          );
        })}
      </View>

      {presets ? (
        <View style={{ gap: 6 }}>
          <Text style={styles.presetLabel}>Presets</Text>
          <View style={styles.presets}>
            {presets.map((p) => (
              <Chip key={p.label} label={p.label} onPress={() => onChange(p.apply(value))} />
            ))}
          </View>
        </View>
      ) : null}

      {value.kind === 'cron' ? (
        <FormField label="Cron expression" hint="5 fields (min hour day month weekday) or @hourly @daily @weekly @monthly @yearly @reboot. Evaluated in UTC.">
          <Input value={value.cron} onChangeText={(cron) => set({ cron })} placeholder="*/5 * * * *" invalid={invalid} />
        </FormField>
      ) : null}

      {value.kind === 'once' ? (
        <FormField label="Run at (UTC, YYYY-MM-DD HH:MM)">
          <Input value={value.onceAt} onChangeText={(onceAt) => set({ onceAt })} placeholder="2026-10-06 10:30" invalid={invalid} />
        </FormField>
      ) : null}

      {value.kind === 'interval' ? (
        <View style={styles.row}>
          <FormField label="Every">
            <Input
              value={value.intervalValue}
              onChangeText={(intervalValue) => set({ intervalValue })}
              keyboardType="numeric"
              invalid={invalid}
              style={{ width: 100 }}
            />
          </FormField>
          {(['seconds', 'minutes', 'hours'] as IntervalUnit[]).map((u) => (
            <Chip key={u} label={u} active={value.intervalUnit === u} onPress={() => set({ intervalUnit: u })} />
          ))}
        </View>
      ) : null}

      {value.kind === 'daily' ? timeInput : null}

      {value.kind === 'weekly' ? (
        <View style={{ gap: 10 }}>
          {timeInput}
          <FormField label="Days (UTC)">
            <View style={styles.presets}>
              {DAYS.map((d, i) => {
                const on = value.days.includes(i);
                return (
                  <Chip
                    key={d}
                    label={d}
                    active={on}
                    onPress={() => set({ days: on ? value.days.filter((x) => x !== i) : [...value.days, i].sort() })}
                  />
                );
              })}
            </View>
          </FormField>
        </View>
      ) : null}

      {value.kind === 'monthly' ? (
        <View style={styles.row}>
          {timeInput}
          <FormField label="Day of month" hint="1-31, or -1 for the last day">
            <Input value={value.dayOfMonth} onChangeText={(dayOfMonth) => set({ dayOfMonth })} keyboardType="numeric" invalid={invalid} style={{ width: 100 }} />
          </FormField>
        </View>
      ) : null}

      {value.kind === 'event' ? (
        <View style={{ gap: 10 }}>
          <View style={styles.presets}>
            <Chip label="Machine online" active={value.event === 'machine_online'} onPress={() => set({ event: 'machine_online' })} />
            <Chip label="Relay start" active={value.event === 'relay_start'} onPress={() => set({ event: 'relay_start' })} />
            <Chip label="After schedule..." active={value.event === 'after_schedule'} onPress={() => set({ event: 'after_schedule' })} />
          </View>
          {value.event === 'after_schedule' ? (
            <View style={{ gap: 10 }}>
              <FormField label="After schedule">
                <View style={styles.presets}>
                  {schedules
                    .filter((s) => s.id !== selfId)
                    .map((s) => (
                      <Chip key={s.id} label={`#${s.id} ${s.name}`} active={value.afterId === String(s.id)} onPress={() => set({ afterId: String(s.id) })} />
                    ))}
                </View>
                <Input value={value.afterId} onChangeText={(afterId) => set({ afterId })} placeholder="schedule id" keyboardType="numeric" invalid={invalid} style={{ width: 140 }} />
              </FormField>
              <FormField label="Run when it">
                <View style={styles.presets}>
                  <Chip label="succeeds" active={value.on === 'success'} onPress={() => set({ on: 'success' })} />
                  <Chip label="fails" active={value.on === 'failure'} onPress={() => set({ on: 'failure' })} />
                  <Chip label="finishes (any)" active={value.on === 'any'} onPress={() => set({ on: 'any' })} />
                </View>
              </FormField>
            </View>
          ) : null}
        </View>
      ) : null}

      {errorMessages.length > 0 ? <FormField errors={errorMessages}>{null}</FormField> : null}

      <View style={styles.preview}>
        <Text style={styles.previewTitle}>Next 5 runs (UTC){previewLoading ? ' ...' : ''}</Text>
        {preview ? (
          <>
            <Text style={styles.previewSummary}>{preview.summary}</Text>
            {preview.next.length === 0 ? (
              <Text style={styles.previewLine}>No scheduled times (fires on an event).</Text>
            ) : (
              preview.next.map((iso) => (
                <Text key={iso} style={styles.previewLine}>
                  {formatUtc(iso)}
                </Text>
              ))
            )}
          </>
        ) : (
          <Text style={styles.previewLine}>{invalid ? 'Fix the trigger to see upcoming runs.' : 'Fill in the trigger to preview.'}</Text>
        )}
      </View>
    </View>
  );
}
