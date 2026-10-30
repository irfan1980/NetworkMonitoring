import React, { useEffect, useRef, useState } from 'react';
import { Text, View } from 'react-native';
import { createThemedStyles } from '../../theme/ThemeContext';
import { colors } from '../../theme/colors';
import { findRun, runSchedule } from '../../services/schedules';
import { useUIStore } from '../../stores/uiStore';
import { describeScheduleError, outcomeColor, outcomeLabel } from '../../utils/schedules';
import type { PinSession } from '../../hooks/usePinSession';
import type { ScheduleRun } from '../../types/schedules';
import { ActionButton } from './ui';

const POLL_MS = 2000;
const POLL_LIMIT_MS = 10 * 60_000;

const useStyles = createThemedStyles((c) => ({
  wrap: { gap: 4, flexShrink: 1 },
  line: { color: c.textSecondary, fontSize: 12 },
}));

type Phase =
  | { kind: 'idle' }
  | { kind: 'starting' }
  | { kind: 'neutral'; text: string }
  | { kind: 'error'; text: string }
  | { kind: 'waiting'; runId: string }
  | { kind: 'done'; run: ScheduleRun };

interface Props {
  scheduleId: number;
  pin: PinSession;
}

export function TestRunButton({ scheduleId, pin }: Props) {
  const styles = useStyles();
  const showError = useUIStore((s) => s.showError);
  const [phase, setPhase] = useState<Phase>({ kind: 'idle' });
  const aliveRef = useRef(true);

  useEffect(() => {
    aliveRef.current = true;
    return () => {
      aliveRef.current = false;
    };
  }, []);

  useEffect(() => {
    if (phase.kind !== 'waiting') return;
    const startedAt = Date.now();
    const timer = setInterval(async () => {
      if (Date.now() - startedAt > POLL_LIMIT_MS) {
        clearInterval(timer);
        setPhase({ kind: 'neutral', text: 'Still running; check the history for the result.' });
        return;
      }
      try {
        const run = await findRun(scheduleId, phase.runId);
        if (run && run.ended_at && aliveRef.current) {
          clearInterval(timer);
          setPhase({ kind: 'done', run });
        }
      } catch {
      }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [phase, scheduleId]);

  async function start() {
    setPhase({ kind: 'starting' });
    try {
      const res = await pin.run((p) => runSchedule(scheduleId, p), 'Test run executes the script on the machine now.');
      if (!aliveRef.current) return;
      if (!res) {
        setPhase({ kind: 'idle' });
        return;
      }
      if (res.status === 'started') setPhase({ kind: 'waiting', runId: res.run_id });
      else if (res.status === 'skipped_locked') setPhase({ kind: 'neutral', text: `Skipped: already running${res.reason ? ` (${res.reason})` : ''}` });
      else setPhase({ kind: 'neutral', text: `Missed${res.reason ? `: ${res.reason}` : ''}` });
    } catch (e) {
      if (!aliveRef.current) return;
      const text = describeScheduleError(e);
      setPhase({ kind: 'error', text });
      showError(text);
    }
  }

  const busy = phase.kind === 'starting' || phase.kind === 'waiting';
  return (
    <View style={styles.wrap}>
      <ActionButton label={busy ? 'Running...' : 'Test run'} onPress={start} disabled={busy} />
      {phase.kind === 'waiting' ? <Text style={styles.line}>Started, waiting for it to finish...</Text> : null}
      {phase.kind === 'neutral' ? <Text style={[styles.line, { color: colors.unknown }]}>{phase.text}</Text> : null}
      {phase.kind === 'error' ? <Text style={[styles.line, { color: colors.bad, fontWeight: '700' }]}>{phase.text}</Text> : null}
      {phase.kind === 'done' ? (
        <Text style={styles.line}>
          Finished:{' '}
          <Text style={{ color: outcomeColor(phase.run.outcome), fontWeight: '800' }}>{outcomeLabel(phase.run.outcome)}</Text>
          {phase.run.exit_code != null ? ` (exit ${phase.run.exit_code})` : ''}
          {phase.run.reason ? ` · ${phase.run.reason}` : ''}
        </Text>
      ) : null}
    </View>
  );
}
