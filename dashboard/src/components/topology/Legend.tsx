import React, { useMemo } from 'react';
import { Text, View } from 'react-native';
import { createThemedStyles } from '../../theme/ThemeContext';
import { colors } from '../../theme/colors';
import { useConnectionStore } from '../../stores/connectionStore';
import { DEFAULT_QUEUE_THRESHOLDS } from '../../constants/queueThresholds';
import type { QueueThresholds } from '../../types/relay';

const useStyles = createThemedStyles((c) => ({
  wrap: { width: '100%', flexDirection: 'row', alignItems: 'center' },
  row: { flexDirection: 'row', alignItems: 'center', gap: 14, paddingVertical: 4, paddingRight: 28 },
  wrapRow: {
    flexDirection: 'row',
    alignItems: 'center',
    flexWrap: 'wrap',
    columnGap: 14,
    rowGap: 2,
    paddingVertical: 4,
    paddingRight: 8,
    flexShrink: 1,
  },
  item: { flexDirection: 'row', alignItems: 'center', gap: 5 },
  dot: { width: 9, height: 9, borderRadius: 5 },
  label: { color: c.textSecondary, fontSize: 11 },
  hint: { fontSize: 14, color: c.textMuted, paddingLeft: 4 },
}));

const itemsFor = (t: QueueThresholds): { color: string; label: string }[] => [
  { color: colors.good, label: 'Green = flowing' },
  { color: colors.warn, label: `Yellow = ${t.yellow}+ waiting` },
  { color: colors.degraded, label: `Orange = ${t.degraded}+ (Degraded)` },
  { color: colors.bad, label: `Red = ${t.red}+ waiting` },
  { color: colors.unknown, label: 'Grey = not responding / waiting' },
  { color: colors.bad, label: 'Red server ring = database down' },
  { color: colors.degraded, label: 'Stopped sender/receiver colours by its own pending count' },
  { color: colors.warnDim, label: '3 parts per string: sender | bus | receiver' },
  { color: colors.warnDim, label: 'Pile = count held there: to send | queued | to apply' },
  { color: colors.bad, label: '✕ N = messages failed permanently' },
  { color: colors.bad, label: 'lost N = not arrived, route quiet (late delivery clears it)' },
  { color: colors.unknown, label: '? = checking, not yet known' },
  { color: colors.packet, label: 'Dots = moving now' },
];

export function Legend() {
  const styles = useStyles();
  const thresholds = useConnectionStore((st) => st.thresholds);

  const items = useMemo(() => itemsFor(thresholds ?? DEFAULT_QUEUE_THRESHOLDS), [thresholds]).map((item) => (
    <View key={item.label} style={styles.item}>
      <View style={[styles.dot, { backgroundColor: item.color }]} />
      <Text style={styles.label}>{item.label}</Text>
    </View>
  ));

  return (
    <View style={styles.wrap}>
      <View style={styles.wrapRow}>{items}</View>
    </View>
  );
}
