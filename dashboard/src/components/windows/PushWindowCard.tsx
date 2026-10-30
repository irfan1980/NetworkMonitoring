import React, { useEffect, useState } from 'react';
import { Text, View } from 'react-native';
import { createThemedStyles } from '../../theme/ThemeContext';
import { colors } from '../../theme/colors';
import { ActionButton } from '../scheduler/ui';
import { WindowFields } from './WindowFields';
import { usePushWindow } from '../../hooks/usePushWindow';
import { useUIStore } from '../../stores/uiStore';
import { describeWindow, deviceTimezone, validateWindow } from '../../utils/timeWindow';
import type { AccountWindow } from '../../types/downtime';

const useStyles = createThemedStyles((c) => ({
  card: { backgroundColor: c.surface, borderRadius: 12, padding: 16, gap: 12 },
  label: { color: c.textSecondary, fontSize: 12, fontWeight: '700' },
  text: { color: c.textPrimary, fontSize: 14 },
  muted: { color: c.textMuted, fontSize: 12 },
  actions: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
}));

const defaultWindow = (): AccountWindow => ({
  timezone: deviceTimezone(),
  days: [0, 1, 2, 3, 4],
  start: '09:00',
  end: '17:00',
});

export function PushWindowCard() {
  const styles = useStyles();
  const showSuccess = useUIStore((s) => s.showSuccess);
  const showError = useUIStore((s) => s.showError);
  const { window, loading, error, save } = usePushWindow();
  const [draft, setDraft] = useState<AccountWindow>(defaultWindow);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (window) setDraft(window);
  }, [window]);

  const onSave = async () => {
    const problem = validateWindow(draft);
    if (problem) {
      showError(problem);
      return;
    }
    setBusy(true);
    try {
      await save(draft);
      showSuccess('Push window saved.');
    } catch (e) {
      showError(e instanceof Error ? e.message : 'Could not save the push window.');
    } finally {
      setBusy(false);
    }
  };

  const onClear = async () => {
    setBusy(true);
    try {
      await save(null);
      setDraft(defaultWindow());
      showSuccess('Push window cleared: pushes are always on.');
    } catch (e) {
      showError(e instanceof Error ? e.message : 'Could not clear the push window.');
    } finally {
      setBusy(false);
    }
  };

  return (
    <View style={styles.card}>
      <Text style={styles.label}>MY PUSH HOURS</Text>
      <Text style={styles.muted}>
        You only get push notifications on this account during these hours, in your own timezone. Alerts still show on screen at any time.
      </Text>
      {loading ? (
        <Text style={styles.muted}>Loading...</Text>
      ) : (
        <>
          {error ? <Text style={[styles.muted, { color: colors.bad }]}>{error}</Text> : null}
          <Text style={styles.text}>{window ? describeWindow(window) : 'Not set: pushes are always on.'}</Text>
          <WindowFields value={draft} onChange={setDraft} />
          <View style={styles.actions}>
            <ActionButton label={busy ? 'Saving...' : 'Save push hours'} tone="primary" onPress={onSave} disabled={busy} />
            {window ? <ActionButton label="Clear (always on)" onPress={onClear} disabled={busy} /> : null}
          </View>
        </>
      )}
    </View>
  );
}
