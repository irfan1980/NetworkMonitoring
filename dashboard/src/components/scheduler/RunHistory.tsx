import React, { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { createThemedStyles } from '../../theme/ThemeContext';
import { formatDuration, formatUtc } from '../../utils/format';
import { outcomeColor, outcomeLabel } from '../../utils/schedules';
import type { ScheduleRun } from '../../types/schedules';
import { ActionButton } from './ui';

const useStyles = createThemedStyles((c) => ({
  list: { gap: 6 },
  run: { backgroundColor: c.background, borderRadius: 8, borderLeftWidth: 4, padding: 10, gap: 6 },
  head: { flexDirection: 'row', alignItems: 'center', gap: 10, flexWrap: 'wrap' },
  outcome: { fontSize: 13, fontWeight: '800' },
  meta: { color: c.textSecondary, fontSize: 12 },
  reason: { color: c.textMuted, fontSize: 12, fontStyle: 'italic' },
  tailLabel: { color: c.textMuted, fontSize: 10, fontWeight: '800', textTransform: 'uppercase', letterSpacing: 0.5 },
  tail: { color: c.textPrimary, fontSize: 12, fontFamily: 'monospace', backgroundColor: c.surface, borderRadius: 6, padding: 8 },
  empty: { color: c.textMuted, fontSize: 13 },
  err: { color: c.textMuted, fontSize: 12 },
}));

function durationText(run: ScheduleRun): string {
  if (!run.ended_at) return 'no end time';
  return formatDuration(new Date(run.ended_at).getTime() - new Date(run.started_at).getTime());
}

function RunItem({ run }: { run: ScheduleRun }) {
  const styles = useStyles();
  const [open, setOpen] = useState(false);
  const color = outcomeColor(run.outcome);
  const hasTail = !!(run.stdout_tail || run.stderr_tail);
  return (
    <View style={[styles.run, { borderLeftColor: color }]}>
      <Pressable onPress={() => setOpen((o) => !o)} style={{ gap: 4 }}>
        <View style={styles.head}>
          <Text style={[styles.outcome, { color }]}>{outcomeLabel(run.outcome)}</Text>
          <Text style={styles.meta}>{formatUtc(run.started_at)}</Text>
          <Text style={styles.meta}>
            {run.trigger.replace('_', ' ')} · {durationText(run)}
            {run.exit_code != null ? ` · exit ${run.exit_code}` : ''}
            {run.attempts > 1 ? ` · ${run.attempts} attempts` : ''}
          </Text>
          <Text style={styles.meta}>{open ? '▲' : '▼'}</Text>
        </View>
        {run.reason ? <Text style={styles.reason}>{run.reason}</Text> : null}
      </Pressable>
      {open && (run.script_path || run.interpreter) ? (
        <View style={{ gap: 2 }}>
          {run.script_path ? <Text style={styles.meta} selectable>Script: {run.script_path}</Text> : null}
          {run.interpreter ? <Text style={styles.meta} selectable>Interpreter: {run.interpreter}</Text> : null}
        </View>
      ) : null}
      {open ? (
        hasTail ? (
          <View style={{ gap: 4 }}>
            {run.stdout_tail ? (
              <>
                <Text style={styles.tailLabel}>stdout (tail)</Text>
                <Text style={styles.tail} selectable>
                  {run.stdout_tail}
                </Text>
              </>
            ) : null}
            {run.stderr_tail ? (
              <>
                <Text style={styles.tailLabel}>stderr (tail)</Text>
                <Text style={styles.tail} selectable>
                  {run.stderr_tail}
                </Text>
              </>
            ) : null}
          </View>
        ) : (
          <Text style={styles.reason}>No output captured.</Text>
        )
      ) : null}
    </View>
  );
}

interface Props {
  runs: ScheduleRun[];
  loading: boolean;
  loadingMore: boolean;
  error: string | null;
  hasMore: boolean;
  onLoadMore: () => void;
}

export function RunHistory({ runs, loading, loadingMore, error, hasMore, onLoadMore }: Props) {
  const styles = useStyles();
  return (
    <View style={styles.list}>
      {loading && runs.length === 0 ? <Text style={styles.empty}>Loading runs...</Text> : null}
      {!loading && runs.length === 0 && !error ? <Text style={styles.empty}>No runs yet.</Text> : null}
      {error ? <Text style={styles.err}>Could not load run history: {error}</Text> : null}
      {runs.map((r) => (
        <RunItem key={r.run_id || r.seq} run={r} />
      ))}
      {hasMore ? <ActionButton label={loadingMore ? 'Loading...' : 'Load older runs'} onPress={onLoadMore} disabled={loadingMore} /> : null}
    </View>
  );
}
