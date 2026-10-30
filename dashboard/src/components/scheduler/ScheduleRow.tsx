import React from 'react';
import { Pressable, Text, View } from 'react-native';
import { createThemedStyles } from '../../theme/ThemeContext';
import { colors } from '../../theme/colors';
import { useConnectionStore } from '../../stores/connectionStore';
import { useResponsive } from '../../hooks/useResponsive';
import { formatTimeAgo, formatTimeUntil, formatUtc } from '../../utils/format';
import { healthColor, machineName, outcomeColor, outcomeLabel } from '../../utils/schedules';
import type { ScheduleSummary } from '../../types/relay';
import { Pill } from './ui';

const useStyles = createThemedStyles((c) => ({
  row: { flexDirection: 'row', backgroundColor: c.surface, borderRadius: 10, overflow: 'hidden' },
  bar: { width: 5 },
  body: { flex: 1, padding: 12, gap: 8 },
  cols: { flexDirection: 'row', gap: 16, alignItems: 'flex-start' },
  colsPhone: { gap: 8 },
  colId: { width: 190, gap: 2 },
  colMachine: { width: 170, gap: 2 },
  colTrigger: { flex: 1, gap: 2 },
  colLast: { width: 190, gap: 2 },
  full: { gap: 2 },
  nameLine: { flexDirection: 'row', alignItems: 'center', gap: 8, flexWrap: 'wrap' },
  id: { color: c.textMuted, fontSize: 12, fontWeight: '800' },
  name: { color: c.textPrimary, fontSize: 14, fontWeight: '800' },
  primary: { color: c.textPrimary, fontSize: 13 },
  secondary: { color: c.textSecondary, fontSize: 12 },
  label: { color: c.textMuted, fontSize: 10, fontWeight: '800', textTransform: 'uppercase', letterSpacing: 0.5 },
  reason: { fontSize: 12, fontStyle: 'italic' },
  actions: { flexDirection: 'row', gap: 8, flexWrap: 'wrap' },
}));

interface Props {
  schedule: ScheduleSummary;
  onPress?: () => void;
  showMachine?: boolean;
  footer?: React.ReactNode;
}

export function ScheduleRow({ schedule, onPress, showMachine = true, footer }: Props) {
  const styles = useStyles();
  const { isPhone } = useResponsive();
  const machines = useConnectionStore((s) => s.machines);
  const color = healthColor(schedule.state);
  const machine = machineName(machines, schedule.machine_id);

  const nameBlock = (
    <View style={isPhone ? styles.full : styles.colId}>
      <View style={styles.nameLine}>
        <Text style={styles.id}>#{schedule.id}</Text>
        <Text style={styles.name}>{schedule.name}</Text>
        {schedule.running ? <Pill label="RUNNING" color={colors.accent} /> : null}
      </View>
    </View>
  );

  const machineBlock = showMachine ? (
    <View style={isPhone ? styles.full : styles.colMachine}>
      <Text style={styles.label}>Machine</Text>
      <Text style={styles.primary}>{machine}</Text>
    </View>
  ) : null;

  const triggerBlock = (
    <View style={isPhone ? styles.full : styles.colTrigger}>
      <Text style={styles.label}>Schedule</Text>
      <Text style={styles.primary}>{schedule.trigger_summary}</Text>
      <Text style={styles.secondary}>
        {schedule.next_run ? `next ${formatUtc(schedule.next_run)} (${formatTimeUntil(schedule.next_run)})` : 'no upcoming run'}
      </Text>
    </View>
  );

  const lastBlock = (
    <View style={isPhone ? styles.full : styles.colLast}>
      <Text style={styles.label}>Last run</Text>
      {schedule.last_run ? (
        <>
          <Text style={styles.primary}>
            {formatTimeAgo(schedule.last_run)} ·{' '}
            <Text style={{ color: outcomeColor(schedule.last_outcome), fontWeight: '800' }}>{outcomeLabel(schedule.last_outcome)}</Text>
          </Text>
          <Text style={styles.secondary}>{formatUtc(schedule.last_run)}</Text>
        </>
      ) : schedule.last_outcome ? (
        <Text style={styles.primary}>
          <Text style={{ color: outcomeColor(schedule.last_outcome), fontWeight: '800' }}>{outcomeLabel(schedule.last_outcome)}</Text>
        </Text>
      ) : (
        <Text style={styles.secondary}>never run</Text>
      )}
    </View>
  );

  const main = (
    <View style={{ gap: 8 }}>
      <View style={[styles.cols, isPhone ? styles.colsPhone : null, isPhone ? { flexDirection: 'column' } : null]}>
        {nameBlock}
        {machineBlock}
        {triggerBlock}
        {lastBlock}
      </View>
      {schedule.reason && !(!schedule.last_run && !schedule.last_outcome && /never run/i.test(schedule.reason)) ? (
        <Text style={[styles.reason, { color: schedule.state === 'red' ? colors.bad : colors.textMuted }]}>
          {schedule.running && schedule.reason === 'waiting for endpoint' ? "Running: waiting for the machine's scheduler endpoint to report back" : schedule.reason}
        </Text>
      ) : null}
    </View>
  );

  return (
    <View style={styles.row}>
      <View style={[styles.bar, { backgroundColor: color }]} />
      <View style={styles.body}>
        {onPress ? <Pressable onPress={onPress}>{main}</Pressable> : main}
        {footer ? <View style={styles.actions}>{footer}</View> : null}
      </View>
    </View>
  );
}
