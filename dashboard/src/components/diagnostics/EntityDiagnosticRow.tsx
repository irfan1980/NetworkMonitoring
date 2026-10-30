import React, { useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { createThemedStyles } from '../../theme/ThemeContext';
import { colors } from '../../theme/colors';
import { formatClock, formatTimeAgo } from '../../utils/format';
import { entityDotColor, entityDotState, troubleshootHint } from '../../utils/diagnosticsStatus';
import type { EntityDiagnostic } from '../../types/diagnostics';

const useStyles = createThemedStyles((c) => ({
  row: { backgroundColor: c.surface, borderRadius: 12, padding: 14, gap: 8 },
  topRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  dot: { width: 12, height: 12, borderRadius: 6 },
  name: { color: c.textPrimary, fontSize: 16, fontWeight: '800', flexShrink: 1 },
  severity: { color: c.textMuted, fontSize: 12, fontWeight: '700' },
  metricsRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 16 },
  metric: { minWidth: 90 },
  metricLabel: { color: c.textMuted, fontSize: 11 },
  metricValue: { color: c.textPrimary, fontSize: 15, fontWeight: '700', marginTop: 1 },
  opsText: { color: c.textSecondary, fontSize: 12 },
  detail: { backgroundColor: c.surfaceRaised, borderRadius: 10, padding: 14, gap: 8, marginTop: 4 },
  detailLine: { color: c.textSecondary, fontSize: 13 },
  detailLabel: { color: c.textMuted, fontSize: 11, fontWeight: '700', textTransform: 'uppercase' },
  errorBox: { backgroundColor: c.badDim, borderRadius: 8, padding: 10, gap: 4 },
  errorText: { color: colors.bad, fontSize: 13, fontWeight: '600' },
  hint: { color: colors.warn, fontSize: 13, fontWeight: '700', fontStyle: 'italic' },
}));

interface Props {
  entity: EntityDiagnostic;
  windowSeconds: number;
  fromLabel: string;
  toLabel: string;
  queuedIncludesLost?: boolean;
  fromDown?: boolean;
  toDown?: boolean;
}

export function EntityDiagnosticRow({ entity, windowSeconds, fromLabel, toLabel, queuedIncludesLost, fromDown, toDown }: Props) {
  const styles = useStyles();
  const [expanded, setExpanded] = useState(false);
  const state = entityDotState(entity, windowSeconds);
  const hint = troubleshootHint(entity, fromLabel, toLabel);
  const na = 'unavailable';
  const pendingText = fromDown ? na : entity.pending;
  const queuedText = toDown ? na : entity.queued;
  const appliedText = toDown ? na : entity.appliedInWindow;
  const headerMeta = [entity.severity == null ? null : `S${entity.severity}`, (entity.operations ?? []).join(', ') || null]
    .filter(Boolean)
    .join(' · ');
  const queuedLabel = queuedIncludesLost ? 'Not matched yet' : 'Queued';

  return (
    <Pressable style={styles.row} onPress={() => setExpanded((v) => !v)}>
      <View style={styles.topRow}>
        <View style={[styles.dot, { backgroundColor: entityDotColor(state) }]} />
        <Text style={styles.name}>{entity.entity}</Text>
        <Text style={styles.severity}>{headerMeta}</Text>
      </View>
      <View style={styles.metricsRow}>
        <View style={styles.metric}>
          <Text style={styles.metricLabel}>Pending</Text>
          <Text style={styles.metricValue}>{pendingText}</Text>
        </View>
        <View style={styles.metric}>
          <Text style={styles.metricLabel}>{queuedLabel}</Text>
          <Text style={styles.metricValue}>{queuedText}</Text>
        </View>
        <View style={styles.metric}>
          <Text style={styles.metricLabel}>Applied (window)</Text>
          <Text style={styles.metricValue}>{appliedText}</Text>
        </View>
        <View style={styles.metric}>
          <Text style={styles.metricLabel}>Errors</Text>
          <Text style={[styles.metricValue, entity.errors > 0 && { color: colors.bad }]}>{entity.errors}</Text>
        </View>
        <View style={styles.metric}>
          <Text style={styles.metricLabel}>Last used</Text>
          <Text style={styles.metricValue}>{formatTimeAgo(entity.lastAppliedAt ?? entity.lastSentAt)}</Text>
        </View>
      </View>

      {expanded ? (
        <View style={styles.detail}>
          <Text style={styles.detailLine}>
            Sender: <Text style={{ fontWeight: '800', color: colors.textPrimary }}>{fromDown ? na : entity.senderFunction ?? na}</Text> on{' '}
            {fromLabel} ({fromDown ? na : entity.senderFormat ?? na})
          </Text>
          <Text style={styles.detailLine}>
            Receiver: <Text style={{ fontWeight: '800', color: colors.textPrimary }}>{toDown ? na : entity.receiverProcedure ?? na}</Text> on{' '}
            {toLabel} ({toDown ? na : entity.receiverFormat ?? na})
          </Text>
          {(entity.operations ?? []).length > 0 || entity.severity != null ? (
            <Text style={styles.detailLine}>
              {[(entity.operations ?? []).length > 0 ? `Operations: ${entity.operations.join(', ')}` : null, entity.severity != null ? `Severity S${entity.severity}` : null]
                .filter(Boolean)
                .join(' · ')}
            </Text>
          ) : null}
          <Text style={styles.detailLine}>
            Last sent: {formatClock(entity.lastSentAt)} ({formatTimeAgo(entity.lastSentAt)})
          </Text>
          <Text style={styles.detailLine}>
            Last applied: {formatClock(entity.lastAppliedAt)} ({formatTimeAgo(entity.lastAppliedAt)})
          </Text>
          <Text style={styles.detailLine}>
            Pending: {pendingText} · {queuedLabel}: {queuedText} · Sent (window): {fromDown ? na : entity.sentInWindow} · Applied
            (window): {appliedText}
          </Text>
          {entity.lastError ? (
            <View style={styles.errorBox}>
              <Text style={styles.detailLabel}>Last error</Text>
              <Text style={styles.errorText}>{entity.lastError.message}</Text>
              <Text style={styles.detailLine}>
                {formatClock(entity.lastError.at)} ({formatTimeAgo(entity.lastError.at)}) · {entity.lastError.messageId ? `message ${entity.lastError.messageId}` : ''}
              </Text>
            </View>
          ) : null}
          {hint ? <Text style={styles.hint}>{hint}</Text> : null}
        </View>
      ) : null}
    </Pressable>
  );
}
