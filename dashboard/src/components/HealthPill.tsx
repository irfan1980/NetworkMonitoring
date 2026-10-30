import React from 'react';
import { Pressable, Text, View } from 'react-native';
import { createThemedStyles } from '../theme/ThemeContext';
import type { OverallStatus } from '../utils/health';

const useStyles = createThemedStyles((c) => ({
  pill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    alignSelf: 'flex-start',
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 8,
    backgroundColor: c.surfaceRaised,
    borderWidth: 1,
    borderColor: c.border,
  },
  dot: { width: 9, height: 9, borderRadius: 5 },
  title: { fontSize: 13, fontWeight: '800', color: c.textPrimary },
}));

interface Props {
  status: OverallStatus;
  onLongPress?: () => void;
}

export function HealthPill({ status, onLongPress }: Props) {
  const styles = useStyles();
  return (
    <Pressable onLongPress={onLongPress} delayLongPress={900}>
      <View style={styles.pill}>
        <View style={[styles.dot, { backgroundColor: status.color }]} />
        <Text style={styles.title}>{status.title}</Text>
      </View>
    </Pressable>
  );
}
