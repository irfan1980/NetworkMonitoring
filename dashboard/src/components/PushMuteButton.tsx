import React, { useState } from 'react';
import { Pressable, Text } from 'react-native';
import { usePushStore } from '../stores/pushStore';
import { useUIStore } from '../stores/uiStore';
import { colors } from '../theme/colors';

interface Props {
  alertId: string;
  alertIds?: string[];
  compact?: boolean;
}

export function PushMuteButton({ alertId, alertIds, compact }: Props) {
  const registered = usePushStore((s) => s.registration !== null);
  const muted = usePushStore((s) => !!s.mutedAlertIds[alertId]);
  const mute = usePushStore((s) => s.mute);
  const unmute = usePushStore((s) => s.unmute);
  const showError = useUIStore((s) => s.showError);
  const showSuccess = useUIStore((s) => s.showSuccess);
  const [busy, setBusy] = useState(false);

  if (!registered) return null;

  const onPress = async () => {
    setBusy(true);
    try {
      for (const id of alertIds ?? [alertId]) await (muted ? unmute(id) : mute(id));
      showSuccess(muted ? 'Push resumed for this alert.' : 'Push stopped for your account. The alert stays visible.');
    } catch (e) {
      showError(e instanceof Error ? e.message : 'Could not update push.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <Pressable
      accessibilityRole="button"
      hitSlop={10}
      disabled={busy}
      onPress={onPress}
      style={{
        paddingHorizontal: 14,
        paddingVertical: compact ? 10 : 8,
        borderRadius: 8,
        backgroundColor: colors.surfaceRaised,
        opacity: busy ? 0.6 : 1,
      }}
    >
      <Text style={{ fontWeight: '700', fontSize: 13, color: colors.textSecondary }}>
        {muted ? 'Resume push' : 'Stop push for my account'}
      </Text>
    </Pressable>
  );
}
