import React from 'react';
import { Platform, Pressable, Text, View } from 'react-native';
import { createThemedStyles } from '../theme/ThemeContext';
import { colors } from '../theme/colors';
import { usePushReadiness } from '../hooks/usePushReadiness';
import { usePushStore } from '../stores/pushStore';
import { openSystemSettings, pushPlatform } from '../push/pushClient';
import { batterySupported, requestBatteryExemption } from '../push/batteryOptimization';

const useStyles = createThemedStyles((c) => ({
  card: { backgroundColor: c.surface, borderRadius: 12, padding: 16, gap: 10 },
  label: { color: c.textSecondary, fontSize: 12, fontWeight: '700' },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 10 },
  rowText: { flex: 1, gap: 2 },
  title: { color: c.textPrimary, fontSize: 14, fontWeight: '600' },
  detail: { color: c.textMuted, fontSize: 12 },
  link: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8, backgroundColor: c.surfaceRaised },
  linkText: { color: c.textPrimary, fontSize: 12, fontWeight: '800' },
}));

interface Item {
  key: string;
  ok: boolean | null;
  title: string;
  detail: string;
  action?: { label: string; onPress: () => void };
}

export function PushReadinessCard() {
  const styles = useStyles();
  const { permission, channel, batteryExempt, refresh } = usePushReadiness();
  const registered = usePushStore((s) => s.registration !== null);
  const pushEnabled = usePushStore((s) => s.pushEnabled);

  const items: Item[] = [];

  const permText =
    permission === null
      ? 'Checking...'
      : permission.status === 'granted'
        ? 'Allowed'
        : permission.status === 'undetermined'
          ? 'Not asked yet; it is requested when you register this device'
          : pushPlatform === 'web'
            ? 'Blocked. Allow notifications for this site in the browser settings.'
            : 'Turned off. Alerts cannot appear until you allow notifications for this app.';
  items.push({
    key: 'permission',
    ok: permission ? permission.status === 'granted' : null,
    title: 'Notification permission',
    detail: permText,
    action:
      permission && permission.status === 'denied' && pushPlatform !== 'web'
        ? { label: 'Open settings', onPress: openSystemSettings }
        : undefined,
  });

  if (channel) {
    items.push({ key: 'channel', ok: channel.ok, title: 'Alerts channel', detail: channel.detail, action: channel.ok ? undefined : { label: 'Open settings', onPress: openSystemSettings } });
  }

  if (batterySupported) {
    items.push({
      key: 'battery',
      ok: batteryExempt,
      title: 'Battery optimization',
      detail:
        batteryExempt === null
          ? 'Unknown on this build. Some phones delay or drop pushes for apps that are battery-optimized.'
          : batteryExempt
            ? 'Exempt: pushes arrive immediately'
            : 'Not exempt: some phones delay or drop pushes in the background',
      action: batteryExempt === true ? undefined : { label: 'Fix', onPress: () => void requestBatteryExemption().then(() => setTimeout(refresh, 1500)) },
    });
  }

  if (Platform.OS === 'ios') {
    items.push({
      key: 'ios',
      ok: null,
      title: 'Time Sensitive alerts',
      detail: 'Alerts are Time Sensitive and break through Focus modes. If one is missed, check Settings > Focus and allow this app, and keep Notifications > Time Sensitive on.',
    });
  }

  items.push({
    key: 'registered',
    ok: registered,
    title: 'Device registered with the relay',
    detail: registered ? 'Yes' : 'No. Register this device below.',
  });
  items.push({
    key: 'relay',
    ok: registered ? pushEnabled : null,
    title: 'Relay push sending',
    detail: !registered ? 'Unknown until registered' : pushEnabled ? 'Enabled' : 'Off on the relay (config or Firebase key)',
  });

  return (
    <View style={styles.card}>
      <Text style={styles.label}>BACKGROUND READINESS</Text>
      {items.map((it) => (
        <View key={it.key} style={styles.row}>
          <View style={styles.rowText}>
            <Text style={styles.title}>
              {it.ok === null ? '? ' : it.ok ? '✓ ' : '✗ '}
              {it.title}
            </Text>
            <Text style={[styles.detail, it.ok === false ? { color: colors.warn } : null]}>{it.detail}</Text>
          </View>
          {it.action ? (
            <Pressable style={styles.link} onPress={it.action.onPress}>
              <Text style={styles.linkText}>{it.action.label}</Text>
            </Pressable>
          ) : null}
        </View>
      ))}
    </View>
  );
}
