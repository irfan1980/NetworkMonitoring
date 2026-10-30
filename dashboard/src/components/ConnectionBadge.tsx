import React from 'react';
import { Text, View } from 'react-native';
import { createThemedStyles } from '../theme/ThemeContext';
import { colors } from '../theme/colors';
import type { ConnectionState } from '../types/relay';

const useStyles = createThemedStyles((c) => ({
  pill: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    paddingHorizontal: 10,
    paddingVertical: 5,
    borderRadius: 999,
    backgroundColor: c.surfaceRaised,
    alignSelf: 'flex-start',
  },
  dot: { width: 8, height: 8, borderRadius: 4 },
  label: { fontSize: 11, fontWeight: '700', color: c.textSecondary, letterSpacing: 0.5 },
}));

interface Props {
  state: ConnectionState;
  source?: 'mock' | 'live' | null;
}

export function ConnectionBadge({ state, source }: Props) {
  const styles = useStyles();

  let label: string;
  let color: string;
  if (state === 'unauthorized') {
    label = 'NOT AUTHORISED';
    color = colors.bad;
  } else if (source === 'mock') {
    label = 'MOCK DATA';
    color = colors.warn;
  } else if (state === 'live' && source === 'live') {
    label = 'LIVE';
    color = colors.good;
  } else if (state === 'reconnecting' || state === 'connecting') {
    label = 'RECONNECTING…';
    color = colors.warn;
  } else {
    label = 'NO CONNECTION';
    color = colors.bad;
  }

  return (
    <View style={styles.pill}>
      <View style={[styles.dot, { backgroundColor: color }]} />
      <Text style={styles.label}>{label}</Text>
    </View>
  );
}
