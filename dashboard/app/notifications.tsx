import React, { useEffect } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { createThemedStyles } from '../src/theme/ThemeContext';
import { colors } from '../src/theme/colors';
import { getPushSupport, pushPlatform } from '../src/push/pushClient';
import { usePushStore } from '../src/stores/pushStore';
import { useUIStore } from '../src/stores/uiStore';
import { useDeviceStore } from '../src/stores/deviceStore';
import { useAuthStore } from '../src/stores/authStore';
import { PushReadinessCard } from '../src/components/PushReadinessCard';
import { ACK_NOTE } from '../src/utils/ackFeedback';
import { PushWindowCard } from '../src/components/windows/PushWindowCard';

const useStyles = createThemedStyles((c) => ({
  screen: { flex: 1, backgroundColor: c.background },
  content: { padding: 16, gap: 14, maxWidth: 560 },
  heading: { color: c.textPrimary, fontSize: 20, fontWeight: '800' },
  card: { backgroundColor: c.surface, borderRadius: 12, padding: 16, gap: 10 },
  label: { color: c.textSecondary, fontSize: 12, fontWeight: '700' },
  text: { color: c.textPrimary, fontSize: 14 },
  muted: { color: c.textMuted, fontSize: 12 },
  input: {
    backgroundColor: c.surfaceRaised,
    color: c.textPrimary,
    borderRadius: 8,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: 14,
  },
  btn: { alignSelf: 'flex-start', paddingHorizontal: 16, paddingVertical: 10, borderRadius: 8 },
  btnText: { fontWeight: '800', fontSize: 13 },
}));

export default function NotificationsScreen() {
  const styles = useStyles();
  const insets = useSafeAreaInsets();
  const support = getPushSupport();

  const busy = usePushStore((s) => s.busy);
  const registration = usePushStore((s) => s.registration);
  const hasToken = usePushStore((s) => s.token !== null);
  const pushEnabled = usePushStore((s) => s.pushEnabled);
  const confirmation = usePushStore((s) => s.confirmation);
  const pushError = usePushStore((s) => s.error);
  const enable = usePushStore((s) => s.enable);
  const disable = usePushStore((s) => s.disable);
  const refreshStatus = usePushStore((s) => s.refreshStatus);
  const showSuccess = useUIStore((s) => s.showSuccess);
  const showError = useUIStore((s) => s.showError);
  const device = useDeviceStore((s) => s.device);
  const account = useAuthStore((s) => s.account);
  const minSeverity = useAuthStore((s) => s.minSeverity);

  useEffect(() => {
    if (hasToken) void refreshStatus().catch(() => {});
  }, [hasToken, refreshStatus]);

  const onRegister = async () => {
    try {
      const reg = await enable();
      showSuccess(`Registered to ${reg.account}${reg.minSeverity !== null ? ` (min severity ${reg.minSeverity})` : ''}`);
    } catch (e) {
      showError(e instanceof Error ? e.message : 'Could not register for push.');
    }
  };

  const onUnregister = async () => {
    try {
      await disable();
      showSuccess('This device no longer receives push notifications.');
    } catch (e) {
      showError(e instanceof Error ? e.message : 'Could not unregister.');
    }
  };

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={[
        styles.content,
        { paddingLeft: 16 + insets.left, paddingRight: 16 + insets.right, paddingBottom: 16 + insets.bottom },
      ]}
    >
      <Text style={styles.heading}>Notifications</Text>

      <View style={styles.card}>
        <Text style={styles.label}>SIGNED IN AS</Text>
        <Text style={styles.text}>
          {account ?? 'account'}
          {minSeverity !== null ? ` (min severity ${minSeverity})` : ''}
        </Text>
      </View>

      <PushReadinessCard />

      <PushWindowCard />

      <View style={styles.card}>
        <Text style={styles.label}>REMINDERS</Text>
        <Text style={styles.muted}>{ACK_NOTE}</Text>
      </View>

      {!support.supported ? (
        <View style={styles.card}>
          <Text style={styles.text}>{support.reason}</Text>
        </View>
      ) : registration ? (
        <View style={styles.card}>
          <Text style={styles.text}>
            Registered to {registration.account}
            {registration.minSeverity !== null ? ` (min severity ${registration.minSeverity})` : ''}
          </Text>
          <Text style={styles.muted}>
            {device} · {pushPlatform}
          </Text>
          {confirmation ? (
            <Text style={[styles.muted, { color: confirmation.startsWith('Test') ? colors.good : colors.warn }]}>{confirmation}</Text>
          ) : null}
          {!pushEnabled ? (
            <Text style={[styles.muted, { color: colors.warn }]}>
              Push is switched off on the relay (or its Firebase key is missing); no notifications will arrive.
            </Text>
          ) : null}
          <Pressable
            style={[styles.btn, { backgroundColor: colors.badDim, opacity: busy ? 0.6 : 1 }]}
            disabled={busy}
            onPress={onUnregister}
          >
            <Text style={[styles.btnText, { color: colors.bad }]}>Unregister this device</Text>
          </Pressable>
        </View>
      ) : (
        <View style={styles.card}>
          <Text style={styles.text}>This device is not receiving push notifications.</Text>
          {pushError ? <Text style={[styles.muted, { color: colors.bad }]}>{pushError}</Text> : null}
          <Pressable
            style={[styles.btn, { backgroundColor: colors.goodDim, opacity: busy ? 0.6 : 1 }]}
            disabled={busy}
            onPress={onRegister}
          >
            <Text style={[styles.btnText, { color: colors.good }]}>{busy ? 'Registering...' : 'Register this device'}</Text>
          </Pressable>
        </View>
      )}
    </ScrollView>
  );
}
