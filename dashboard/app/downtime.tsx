import React, { useEffect, useMemo, useState } from 'react';
import { ScrollView, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { createThemedStyles } from '../src/theme/ThemeContext';
import { colors } from '../src/theme/colors';
import { ActionButton, Chip, FormField, Input, Pill } from '../src/components/scheduler/ui';
import { WindowFields } from '../src/components/windows/WindowFields';
import { useDowntime } from '../src/hooks/useDowntime';
import { useConnectionStore } from '../src/stores/connectionStore';
import { useUIStore } from '../src/stores/uiStore';
import { describeWindow, deviceTimezone, isWindowActive, validateWindow } from '../src/utils/timeWindow';
import type { MachineInfo } from '../src/types/relay';
import type { DowntimeInput, DowntimeWindow } from '../src/types/downtime';

const useStyles = createThemedStyles((c) => ({
  screen: { flex: 1, backgroundColor: c.background },
  content: { padding: 16, gap: 14, maxWidth: 720 },
  heading: { color: c.textPrimary, fontSize: 20, fontWeight: '800' },
  notice: { backgroundColor: colors.warnDim, borderRadius: 10, padding: 12 },
  noticeText: { color: colors.warn, fontSize: 13 },
  card: { backgroundColor: c.surface, borderRadius: 12, padding: 14, gap: 8 },
  titleRow: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', gap: 8 },
  title: { color: c.textPrimary, fontSize: 15, fontWeight: '800' },
  text: { color: c.textPrimary, fontSize: 13 },
  muted: { color: c.textMuted, fontSize: 12 },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
}));

const MATCHABLE_TYPES = new Set(['database', 'pos']);
const matchableNodes = (m: MachineInfo | undefined) =>
  (m?.nodes ?? []).filter((n) => MATCHABLE_TYPES.has(String(n.node_type).toLowerCase()));

const emptyDraft = (machine: string): DowntimeInput => ({
  machine,
  service: null,
  timezone: deviceTimezone(),
  days: [0, 1, 2, 3, 4],
  start: '02:00',
  end: '04:00',
  note: '',
});

export default function DowntimeScreen() {
  const styles = useStyles();
  const insets = useSafeAreaInsets();
  const machines = useConnectionStore((s) => s.machines);
  const showSuccess = useUIStore((s) => s.showSuccess);
  const showError = useUIStore((s) => s.showError);
  const { windows, loading, error, create, update, remove } = useDowntime();

  const [editing, setEditing] = useState<{ id: DowntimeWindow['id'] | null; draft: DowntimeInput } | null>(null);
  const [confirmId, setConfirmId] = useState<DowntimeWindow['id'] | null>(null);
  const [busy, setBusy] = useState(false);
  const [now, setNow] = useState(() => new Date());

  const machineList = useMemo(() => Object.values(machines).sort((a, b) => a.machine_name.localeCompare(b.machine_name)), [machines]);
  const machineName = (id: string) => machineList.find((m) => String(m.machine_id) === id)?.machine_name ?? id;
  const serviceLabel = useMemo(() => {
    const map: Record<string, string> = {};
    for (const m of machineList) for (const n of matchableNodes(m)) map[`${m.machine_id}|${n.node_id}`] = n.node_name;
    return map;
  }, [machineList]);

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30000);
    return () => clearInterval(t);
  }, []);

  const startNew = () => setEditing({ id: null, draft: emptyDraft(machineList[0] ? String(machineList[0].machine_id) : '') });
  const startEdit = (w: DowntimeWindow) => {
    const { id, ...draft } = w;
    setEditing({ id, draft });
  };

  const onSave = async () => {
    if (!editing) return;
    const { draft } = editing;
    const problem = !draft.machine.trim() ? 'Pick a machine.' : validateWindow(draft);
    if (problem) {
      showError(problem);
      return;
    }
    setBusy(true);
    try {
      if (editing.id === null) await create(draft);
      else await update(editing.id, draft);
      showSuccess('Downtime window saved.');
      setEditing(null);
    } catch (e) {
      showError(e instanceof Error ? e.message : 'Could not save the downtime window.');
    } finally {
      setBusy(false);
    }
  };

  const onDelete = async (id: DowntimeWindow['id']) => {
    if (confirmId !== id) {
      setConfirmId(id);
      return;
    }
    setBusy(true);
    try {
      await remove(id);
      showSuccess('Downtime window deleted.');
    } catch (e) {
      showError(e instanceof Error ? e.message : 'Could not delete the downtime window.');
    } finally {
      setConfirmId(null);
      setBusy(false);
    }
  };

  const draft = editing?.draft;
  const draftMachine = draft ? machineList.find((m) => String(m.machine_id) === draft.machine) : undefined;

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={[
        styles.content,
        { paddingLeft: 16 + insets.left, paddingRight: 16 + insets.right, paddingBottom: 16 + insets.bottom },
      ]}
    >
      <Text style={styles.heading}>Downtime</Text>
      <View style={styles.notice}>
        <Text style={styles.noticeText}>
          Downtime only stops push notifications for that machine or service, for every account. Alerts still show on screen.
        </Text>
      </View>

      {editing && draft ? (
        <View style={styles.card}>
          <Text style={styles.title}>{editing.id === null ? 'New downtime window' : 'Edit downtime window'}</Text>
          <FormField label="Machine">
            <View style={styles.row}>
              {machineList.map((m) => (
                <Chip
                  key={m.machine_id}
                  label={m.machine_name}
                  active={draft.machine === String(m.machine_id)}
                  onPress={() => setEditing({ ...editing, draft: { ...draft, machine: String(m.machine_id), service: null } })}
                />
              ))}
              {machineList.length === 0 ? <Text style={styles.muted}>No machines known yet.</Text> : null}
            </View>
            {draft.machine && !draftMachine ? <Text style={styles.muted}>Current: machine {draft.machine}</Text> : null}
          </FormField>
          <FormField label="Applies to">
            <View style={styles.row}>
              <Chip label="Whole machine" active={draft.service === null} onPress={() => setEditing({ ...editing, draft: { ...draft, service: null } })} />
              {matchableNodes(draftMachine).map((n) => (
                <Chip
                  key={n.node_id}
                  label={n.node_name}
                  active={draft.service === n.node_id}
                  onPress={() => setEditing({ ...editing, draft: { ...draft, service: n.node_id } })}
                />
              ))}
            </View>
            <Text style={styles.muted}>
              Whole machine covers machine, scheduler, schedule, database and pipeline alerts on it. A service window covers that database/POS node's alerts and pipelines where it is source or target. Service Bus alerts can't be put in downtime.
            </Text>
          </FormField>
          <WindowFields value={draft} onChange={(w) => setEditing({ ...editing, draft: { ...draft, ...w } })} />
          <FormField label="Note">
            <Input
              value={draft.note}
              onChangeText={(note) => setEditing({ ...editing, draft: { ...draft, note } })}
              placeholder="Why (optional)"
              autoCapitalize="sentences"
            />
          </FormField>
          <View style={styles.actions}>
            <ActionButton label={busy ? 'Saving...' : 'Save'} tone="primary" onPress={onSave} disabled={busy} />
            <ActionButton label="Cancel" onPress={() => setEditing(null)} disabled={busy} />
          </View>
        </View>
      ) : (
        <View style={styles.actions}>
          <ActionButton label="Add downtime window" tone="primary" onPress={startNew} />
        </View>
      )}

      {loading ? <Text style={styles.muted}>Loading...</Text> : null}
      {error ? <Text style={[styles.muted, { color: colors.bad }]}>{error}</Text> : null}
      {!loading && !error && windows.length === 0 ? <Text style={styles.muted}>No downtime windows. Pushes are never suppressed.</Text> : null}

      {windows.map((w) => {
        const active = isWindowActive(w, now);
        const neverMatches =
          w.service !== null && machineList.length > 0 && !matchableNodes(machineList.find((m) => String(m.machine_id) === w.machine)).some((n) => n.node_id === w.service);
        return (
          <View key={String(w.id)} style={styles.card}>
            <View style={styles.titleRow}>
              <Text style={styles.title}>
                {machineName(w.machine)}
                {w.service ? ` / ${serviceLabel[`${w.machine}|${w.service}`] ?? w.service}` : ' (whole machine)'}
              </Text>
              {active ? <Pill label="IN DOWNTIME NOW" color={colors.warn} /> : null}
              {neverMatches ? <Pill label="NEVER MATCHES" color={colors.bad} /> : null}
            </View>
            <Text style={styles.text}>{describeWindow(w)}</Text>
            {w.note ? <Text style={styles.muted}>{w.note}</Text> : null}
            <View style={styles.actions}>
              <ActionButton label="Edit" onPress={() => startEdit(w)} disabled={busy} />
              <ActionButton label={confirmId === w.id ? 'Tap again to delete' : 'Delete'} tone="danger" onPress={() => void onDelete(w.id)} disabled={busy} />
            </View>
          </View>
        );
      })}
    </ScrollView>
  );
}
