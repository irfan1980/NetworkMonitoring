import React, { useEffect, useRef, useState } from 'react';
import { Text, View } from 'react-native';
import { createThemedStyles } from '../../theme/ThemeContext';
import { colors } from '../../theme/colors';
import { stopSchedule } from '../../services/schedules';
import { useUIStore } from '../../stores/uiStore';
import { describeScheduleError } from '../../utils/schedules';
import type { PinSession } from '../../hooks/usePinSession';
import { ActionButton } from './ui';

const useStyles = createThemedStyles((c) => ({
  wrap: { gap: 4, flexShrink: 1 },
  row: { flexDirection: 'row', gap: 8, alignItems: 'center', flexWrap: 'wrap' },
  line: { color: c.textSecondary, fontSize: 12 },
  hint: { color: c.textMuted, fontSize: 11, fontStyle: 'italic' },
}));

interface Props {
  scheduleId: number;
  pin: PinSession;
}

export function StopRunButton({ scheduleId, pin }: Props) {
  const styles = useStyles();
  const showError = useUIStore((s) => s.showError);
  const [confirming, setConfirming] = useState(false);
  const [busy, setBusy] = useState(false);
  const [result, setResult] = useState<{ text: string; error?: boolean } | null>(null);
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  async function stop() {
    setBusy(true);
    try {
      const res = await pin.run((p) => stopSchedule(scheduleId, p), 'Stopping the running script.');
      if (!aliveRef.current) return;
      setConfirming(false);
      if (res) setResult({ text: res.status === 'cancelled' ? 'Run cancelled.' : 'Stopping, waiting for the script to exit...' });
    } catch (e) {
      if (!aliveRef.current) return;
      setConfirming(false);
      const text = describeScheduleError(e);
      setResult({ text, error: true });
      showError(text);
    } finally {
      if (aliveRef.current) setBusy(false);
    }
  }

  return (
    <View style={styles.wrap}>
      {confirming ? (
        <View style={styles.row}>
          <Text style={styles.line}>Stop this run?</Text>
          <ActionButton label={busy ? 'Stopping...' : 'Yes, stop'} tone="danger" onPress={stop} disabled={busy} />
          <ActionButton label="Keep running" onPress={() => setConfirming(false)} disabled={busy} />
        </View>
      ) : (
        <ActionButton label="Stop run" tone="danger" onPress={() => { setResult(null); setConfirming(true); }} />
      )}
      {confirming ? (
        <Text style={styles.hint}>If the machine's endpoint is down, the run is abandoned relay-side and the script may still be running on the machine.</Text>
      ) : null}
      {result ? <Text style={[styles.line, result.error ? { color: colors.bad, fontWeight: '700' } : null]}>{result.text}</Text> : null}
    </View>
  );
}
