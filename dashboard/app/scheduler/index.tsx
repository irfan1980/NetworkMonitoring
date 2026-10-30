import React, { useMemo, useState } from 'react';
import { ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { createThemedStyles } from '../../src/theme/ThemeContext';
import { useConnectionStore } from '../../src/stores/connectionStore';
import { machineName, sortSchedules } from '../../src/utils/schedules';
import { ScheduleRow } from '../../src/components/scheduler/ScheduleRow';
import { ActionButton, Chip } from '../../src/components/scheduler/ui';

const useStyles = createThemedStyles((c) => ({
  screen: { flex: 1, backgroundColor: c.background },
  content: { padding: 16, gap: 12, flexGrow: 1 },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' },
  heading: { color: c.textPrimary, fontSize: 20, fontWeight: '800' },
  filters: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  empty: { color: c.textMuted, fontSize: 14 },
  list: { gap: 8 },
}));

export default function SchedulerScreen() {
  const styles = useStyles();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const schedules = useConnectionStore((s) => s.schedules);
  const machines = useConnectionStore((s) => s.machines);
  const [machineFilter, setMachineFilter] = useState<number | null>(null);

  const all = useMemo(() => Object.values(schedules), [schedules]);
  const visible = useMemo(
    () => sortSchedules(machineFilter == null ? all : all.filter((s) => s.machine_id === machineFilter)),
    [all, machineFilter]
  );
  const filterMachineIds = useMemo(() => [...new Set(all.map((s) => s.machine_id))].sort((a, b) => a - b), [all]);
  const anySchedulerMachine = useMemo(() => Object.values(machines).some((m) => m.machine_scheduler), [machines]);

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={[styles.content, { paddingLeft: 16 + insets.left, paddingRight: 16 + insets.right, paddingBottom: 16 + insets.bottom }]}
    >
      <View style={styles.headerRow}>
        <Text style={styles.heading}>Scheduler ({all.length})</Text>
        <ActionButton label="New schedule" tone="primary" onPress={() => router.push('/scheduler/new')} />
      </View>

      {filterMachineIds.length > 1 ? (
        <View style={styles.filters}>
          <Chip label="All machines" active={machineFilter == null} onPress={() => setMachineFilter(null)} />
          {filterMachineIds.map((id) => (
            <Chip key={id} label={machineName(machines, id)} active={machineFilter === id} onPress={() => setMachineFilter(id)} />
          ))}
        </View>
      ) : null}

      {visible.length === 0 ? (
        <Text style={styles.empty}>
          {anySchedulerMachine ? 'No schedules yet. Create one with "New schedule".' : 'No machine has the scheduler enabled, so there is nothing to schedule.'}
        </Text>
      ) : (
        <View style={styles.list}>
          {visible.map((s) => (
            <ScheduleRow key={s.id} schedule={s} onPress={() => router.push(`/scheduler/${s.id}`)} />
          ))}
        </View>
      )}
    </ScrollView>
  );
}
