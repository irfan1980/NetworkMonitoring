import React from 'react';
import { Text, View } from 'react-native';
import { createThemedStyles } from '../theme/ThemeContext';
import { colors } from '../theme/colors';
import { useUIStore } from '../stores/uiStore';
import type { ToastKind } from '../stores/uiStore';

const KIND_COLOR: Record<ToastKind, string> = {
  success: colors.good,
  error: colors.bad,
  warning: colors.warn,
  info: colors.blocked,
};

const useStyles = createThemedStyles((c) => ({
  container: { position: 'absolute', top: 12, right: 12, gap: 8, zIndex: 999, pointerEvents: 'box-none' },
  toast: {
    backgroundColor: c.surfaceRaised,
    borderLeftWidth: 4,
    borderRadius: 8,
    paddingVertical: 10,
    paddingHorizontal: 14,
    maxWidth: 320,
  },
  text: { color: c.textPrimary, fontSize: 13 },
}));

export function ToastHost() {
  const toasts = useUIStore((s) => s.toasts);
  const styles = useStyles();
  if (toasts.length === 0) return null;
  return (
    <View style={styles.container}>
      {toasts.map((t) => (
        <View key={t.id} style={[styles.toast, { borderLeftColor: KIND_COLOR[t.kind] }]}>
          <Text style={styles.text}>{t.message}</Text>
        </View>
      ))}
    </View>
  );
}
