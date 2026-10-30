import React, { useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { createThemedStyles } from '../../src/theme/ThemeContext';
import { colors } from '../../src/theme/colors';
import { useConnectionStore } from '../../src/stores/connectionStore';
import { useUIStore } from '../../src/stores/uiStore';
import { usePinSession } from '../../src/hooks/usePinSession';
import { useScheduleDetail } from '../../src/hooks/useScheduleDetail';
import { useScheduleRuns } from '../../src/hooks/useScheduleRuns';
import { deleteSchedule, ScheduleApiError, setScheduleEnabled } from '../../src/services/schedules';
import { formatTimeAgo, formatTimeUntil, formatUtc } from '../../src/utils/format';
import { describeScheduleError, healthColor, machineName, outcomeColor, outcomeLabel } from '../../src/utils/schedules';
import { RunHistory } from '../../src/components/scheduler/RunHistory';
import { ScheduleEditor } from '../../src/components/scheduler/ScheduleEditor';
import { StopRunButton } from '../../src/components/scheduler/StopRunButton';
import { TestRunButton } from '../../src/components/scheduler/TestRunButton';
import { ActionButton, Pill } from '../../src/components/scheduler/ui';
import type { ScheduleDetail } from '../../src/types/schedules';

const useStyles = createThemedStyles((c) => ({
  screen: { flex: 1, backgroundColor: c.background },
  content: { padding: 16, gap: 16 },
  back: { color: c.textSecondary, fontSize: 13, fontWeight: '700' },
  headerRow: { flexDirection: 'row', alignItems: 'center', gap: 10, flexWrap: 'wrap' },
  name: { color: c.textPrimary, fontSize: 24, fontWeight: '900', flexShrink: 1 },
  idText: { color: c.textMuted, fontSize: 16, fontWeight: '800' },
  section: { backgroundColor: c.surface, borderRadius: 12, padding: 14, gap: 10 },
  sectionTitle: { color: c.textMuted, fontSize: 12, fontWeight: '800', textTransform: 'uppercase', letterSpacing: 0.6 },
  kv: { flexDirection: 'row', justifyContent: 'space-between', gap: 12 },
  k: { color: c.textMuted, fontSize: 12 },
  v: { color: c.textPrimary, fontSize: 13, fontWeight: '700', flexShrink: 1, textAlign: 'right' },
  reason: { fontSize: 13, fontStyle: 'italic' },
  actions: { flexDirection: 'row', gap: 10, flexWrap: 'wrap', alignItems: 'flex-start' },
  confirm: { flexDirection: 'row', gap: 10, alignItems: 'center', flexWrap: 'wrap' },
  confirmText: { color: colors.bad, fontSize: 13, fontWeight: '700' },
  banner: { borderRadius: 8, padding: 10, backgroundColor: colors.badDim },
  bannerText: { color: colors.bad, fontSize: 13, fontWeight: '700' },
  muted: { color: c.textMuted, fontSize: 14 },
}));

function KV({ k, v, color }: { k: string; v: string; color?: string }) {
  const styles = useStyles();
  return (
    <View style={styles.kv}>
      <Text style={styles.k}>{k}</Text>
      <Text style={[styles.v, color ? { color } : null]} selectable>
        {v}
      </Text>
    </View>
  );
}

function triggerDetailText(d: ScheduleDetail): string {
  const t = d.trigger;
  switch (t.kind) {
    case 'cron':
      return `cron ${t.cron}`;
    case 'once':
      return `once at ${formatUtc(t.at)}`;
    case 'interval':
      return `every ${t.every_seconds}s${t.start_at ? `, from ${formatUtc(t.start_at)}` : ''}`;
    case 'daily':
      return `daily at ${t.time} UTC`;
    case 'weekly':
      return `weekly at ${t.time} UTC on ${(t.days_of_week ?? []).map((n) => ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][n]).join(', ')}`;
    case 'monthly':
      return `monthly at ${t.time} UTC on ${t.day_of_month === -1 ? 'the last day' : `day ${t.day_of_month}`}`;
    case 'event':
      return t.event === 'after_schedule' ? `after #${t.after_schedule_id} (${t.on})` : String(t.event).replace('_', ' ');
  }
}

export default function ScheduleDetailScreen() {
  const styles = useStyles();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const { id: idParam } = useLocalSearchParams<{ id: string }>();
  const id = Number(idParam);
  const showSuccess = useUIStore((s) => s.showSuccess);
  const showError = useUIStore((s) => s.showError);

  const row = useConnectionStore((s) => s.schedules[String(id)]);
  const machines = useConnectionStore((s) => s.machines);
  const pin = usePinSession();

  const refreshKey = `${row?.last_run}|${row?.running}|${row?.state}|${row?.next_run}`;
  const { detail, error, loading, reload, setDetail } = useScheduleDetail(id, refreshKey);
  const history = useScheduleRuns(id, refreshKey);

  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  const frame = { paddingLeft: 16 + insets.left, paddingRight: 16 + insets.right, paddingBottom: 16 + insets.bottom };
  const back = (
    <Pressable onPress={() => router.replace('/scheduler')}>
      <Text style={styles.back}>← All schedules</Text>
    </Pressable>
  );

  if (!Number.isFinite(id)) {
    return (
      <ScrollView style={styles.screen} contentContainerStyle={[styles.content, frame]}>
        {back}
        <Text style={styles.muted}>Invalid schedule id.</Text>
      </ScrollView>
    );
  }

  const notFound = error instanceof ScheduleApiError && error.status === 404;
  if (!detail) {
    return (
      <ScrollView style={styles.screen} contentContainerStyle={[styles.content, frame]}>
        {back}
        <Text style={styles.muted}>
          {notFound ? `Schedule #${id} was not found. It may have been deleted.` : error ? `Could not load schedule: ${describeScheduleError(error)}` : loading ? 'Loading schedule...' : ''}
        </Text>
      </ScrollView>
    );
  }

  const live = row ?? detail;
  const color = healthColor(live.state);

  async function toggleEnabled() {
    if (!detail) return;
    setBusy(true);
    setActionError(null);
    try {
      const next = await pin.run((p) => setScheduleEnabled(id, !detail.enabled, p));
      if (next) {
        setDetail(next);
        showSuccess(next.enabled ? 'Schedule enabled' : 'Schedule disabled');
      }
    } catch (e) {
      const text = describeScheduleError(e);
      setActionError(text);
      showError(text);
    } finally {
      setBusy(false);
    }
  }

  async function doDelete() {
    setBusy(true);
    setActionError(null);
    try {
      const res = await pin.run((p) => deleteSchedule(id, p), `Deleting schedule #${id}.`);
      if (res) {
        showSuccess(`Schedule #${id} deleted`);
        router.replace('/scheduler');
      } else {
        setConfirmDelete(false);
      }
    } catch (e) {
      setConfirmDelete(false);
      const text = describeScheduleError(e);
      setActionError(text);
      showError(text);
    } finally {
      setBusy(false);
    }
  }

  if (editing) {
    return (
      <ScrollView style={styles.screen} keyboardShouldPersistTaps="handled" contentContainerStyle={[styles.content, frame]}>
        <Text style={styles.name}>Edit #{detail.id} {detail.name}</Text>
        <ScheduleEditor
          initial={detail}
          pin={pin}
          onSaved={(d) => {
            setDetail(d);
            setEditing(false);
            reload();
          }}
          onCancel={() => setEditing(false)}
        />
        {pin.modal}
      </ScrollView>
    );
  }

  const eff = detail.effective;
  const ctl = detail.controls;
  const src = detail.effective_source;
  const origin = (key: keyof typeof eff, setByUser: boolean) => {
    const s = src?.[key] ?? (setByUser ? 'schedule' : 'default');
    return s === 'schedule' ? '' : s === 'machine' ? ' (machine default)' : ' (default)';
  };
  const withDefault = (set: number | null | undefined, def: number, key: keyof typeof eff, unit = '') =>
    `${set != null ? set : def}${unit}${origin(key, set != null)}`;
  const timeoutText = () => {
    const value = ctl.timeout_s ?? eff.timeout_s;
    const text = detail.timeout_display ?? (value === 0 ? 'none' : `${value} s`);
    return `${text}${origin('timeout_s', ctl.timeout_s != null)}`;
  };

  return (
    <ScrollView style={styles.screen} contentContainerStyle={[styles.content, frame]}>
      {back}
      <View style={styles.headerRow}>
        <Text style={styles.idText}>#{detail.id}</Text>
        <Text style={styles.name}>{detail.name}</Text>
        <Pill label={live.state.toUpperCase()} color={color} />
        {live.running ? <Pill label="RUNNING" color={colors.accent} /> : null}
        {!detail.enabled ? <Pill label="DISABLED" color={colors.unknown} /> : null}
      </View>
      {live.reason ? <Text style={[styles.reason, { color: live.state === 'red' ? colors.bad : colors.textMuted }]}>{live.reason}</Text> : null}

      <View style={styles.section}>
        <View style={styles.actions}>
          <TestRunButton scheduleId={id} pin={pin} />
          {live.running ? <StopRunButton scheduleId={id} pin={pin} /> : null}
          <ActionButton label={detail.enabled ? 'Disable' : 'Enable'} onPress={toggleEnabled} disabled={busy} />
          <ActionButton label="Edit" onPress={() => setEditing(true)} disabled={busy} />
          {confirmDelete ? (
            <View style={styles.confirm}>
              <Text style={styles.confirmText}>Delete this schedule?</Text>
              <ActionButton label="Yes, delete" tone="danger" onPress={doDelete} disabled={busy} />
              <ActionButton label="Keep" onPress={() => setConfirmDelete(false)} disabled={busy} />
            </View>
          ) : (
            <ActionButton label="Delete" tone="danger" onPress={() => setConfirmDelete(true)} disabled={busy} />
          )}
        </View>
        {actionError ? (
          <View style={styles.banner}>
            <Text style={styles.bannerText}>{actionError}</Text>
          </View>
        ) : null}
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Schedule</Text>
        <KV k="Machine" v={machineName(machines, detail.machine_id)} />
        <KV k="Script" v={detail.script} />
        <KV k="Arguments" v={detail.args.length ? detail.args.join(' ') : 'none'} />
        <KV k="Trigger" v={triggerDetailText(detail)} />
        <KV k="Summary" v={live.trigger_summary} />
        <KV k="Next run" v={live.next_run ? `${formatUtc(live.next_run)} (${formatTimeUntil(live.next_run)})` : 'none upcoming'} />
        <KV
          k="Last run"
          v={live.last_run ? `${formatUtc(live.last_run)} (${formatTimeAgo(live.last_run)}) · ${outcomeLabel(live.last_outcome)}` : 'never run'}
          color={live.last_run ? outcomeColor(live.last_outcome) : undefined}
        />
        <KV k="Consecutive failures" v={`${live.consecutive_failures} of ${live.fail_threshold}`} color={live.state === 'red' ? colors.bad : undefined} />
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Run controls (effective)</Text>
        <KV k="Timeout" v={timeoutText()} />
        <KV k="Retries" v={withDefault(ctl.retries, eff.retries, 'retries')} />
        <KV k="Retry delay" v={withDefault(ctl.retry_delay_s, eff.retry_delay_s, 'retry_delay_s', ' s')} />
        <KV k="No overlap" v={ctl.no_overlap ? 'yes' : 'no'} />
        <KV k="Run if missed" v={`${(ctl.run_if_missed ?? eff.run_if_missed) ? 'yes' : 'no'}${origin('run_if_missed', ctl.run_if_missed != null)}`} />
        <KV k="Failure threshold" v={withDefault(detail.stored_fail_threshold, eff.fail_threshold, 'fail_threshold')} />
        <KV k="Active window" v={ctl.active_from || ctl.active_until ? `${ctl.active_from ? formatUtc(ctl.active_from) : 'always'} to ${ctl.active_until ? formatUtc(ctl.active_until) : 'no end'}` : 'always'} />
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Run history</Text>
        <RunHistory
          runs={history.runs}
          loading={history.loading}
          loadingMore={history.loadingMore}
          error={history.error}
          hasMore={history.hasMore}
          onLoadMore={history.loadMore}
        />
      </View>
      {pin.modal}
    </ScrollView>
  );
}
