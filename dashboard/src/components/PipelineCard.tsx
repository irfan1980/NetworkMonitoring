import React from 'react';
import { Pressable, Text, View } from 'react-native';
import { createThemedStyles } from '../theme/ThemeContext';
import { colors } from '../theme/colors';
import { pipelineHealthColor, humanizeRawReason, metricsStaleness, pipelineDisplayName, staleMetricsNote } from '../utils/health';
import { formatAgeSeconds, formatLag } from '../utils/format';
import { countColor } from '../constants/queueThresholds';
import { useConnectionStore } from '../stores/connectionStore';
import type { Pipeline } from '../types/relay';

const useStyles = createThemedStyles((c) => ({
  card: {
    backgroundColor: c.surface,
    borderRadius: 14,
    padding: 16,
    borderWidth: 2,
    borderColor: c.border,
    minWidth: 240,
    flexGrow: 1,
    flexBasis: '30%',
    justifyContent: 'space-between',
  },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  name: { fontSize: 16, fontWeight: '800', color: c.textPrimary },
  rawName: { fontSize: 11, color: c.textMuted, marginTop: 1 },
  badge: { paddingHorizontal: 10, paddingVertical: 4, borderRadius: 999 },
  badgeText: { fontSize: 12, fontWeight: '800' },
  causeCaption: { marginTop: 8, fontSize: 15, fontWeight: '800' },
  metricsRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 12, marginTop: 10 },
  metric: { minWidth: 80 },
  metricLabel: { fontSize: 10, color: c.textMuted, textTransform: 'uppercase', letterSpacing: 0.5 },
  metricValue: { fontSize: 15, color: c.textPrimary, fontWeight: '700', marginTop: 2 },
  blockedCaption: { marginTop: 8, fontSize: 12, color: c.blocked },
  staleNote: { marginTop: 8, fontSize: 11, color: c.textMuted, fontStyle: 'italic' },
}));

interface Props {
  pipeline: Pipeline;
  onPress?: () => void;
  minHeight?: number;
  large?: boolean;
}

function lagCause(p: Pipeline): string | null {
  if (p.sendLagSeconds != null && p.sendLagSeconds > 0) return `${formatLag(p.sendLagSeconds)} send lag`;
  if (p.applyLagSeconds != null && p.applyLagSeconds > 0) return `${formatLag(p.applyLagSeconds)} apply lag`;
  return null;
}

function degradedCause(p: Pipeline): string | null {
  const queued = p.queuedCount ?? 0;
  const lost = p.lostCount ?? 0;
  const pendingInbox = p.pendingInbox ?? 0;
  switch (p.degradedBy) {
    case 'failed_rows':
      if (p.failedOutbox > 0) return `${p.failedOutbox} failed to send`;
      break;
    case 'unresolved_rows':
      if (p.unresolvedInbox > 0) return `${p.unresolvedInbox} failed to process`;
      break;
    case 'backlog':
      if (p.pendingOutbox > 0) return `${p.pendingOutbox} waiting to send`;
      if (pendingInbox > 0) return `${pendingInbox} waiting to be applied`;
      break;
    case 'lag': {
      const lag = lagCause(p);
      if (lag) return lag;
      break;
    }
    case 'in_bus_pile':
      if (queued > 0) return `${queued} waiting in the service bus`;
      break;
    case 'lost_messages':
      if (lost > 0) return `${lost} sent and not arrived`;
      break;
    default:
      break;
  }
  if (p.failedOutbox > 0) return `${p.failedOutbox} failed to send`;
  if (p.unresolvedInbox > 0) return `${p.unresolvedInbox} failed to process`;
  if (p.pendingOutbox > 0) return `${p.pendingOutbox} waiting to send`;
  if (pendingInbox > 0) return `${pendingInbox} waiting to be applied`;
  if (queued > 0) return `${queued} waiting in the service bus`;
  if (lost > 0) return `${lost} sent and not arrived`;
  return lagCause(p);
}

export function PipelineCard({ pipeline: p, onPress, minHeight, large }: Props) {
  const styles = useStyles();
  const platforms = useConnectionStore((s) => s.platforms);
  const color = pipelineHealthColor(p);
  const isRedBlocked = p.health === 'Down' || p.health === 'Degraded';
  const cause = p.health === 'Degraded' ? degradedCause(p) : null;
  const failedAndErrors = p.failedOutbox + (p.failedInbox ?? p.unresolvedInbox);
  const inBus = p.queuedCount ?? 0;
  const lost = p.lostCount ?? 0;
  const { display: displayName, raw: rawName } = pipelineDisplayName(p, platforms);
  const stale = metricsStaleness(p);

  return (
    <Pressable
      onPress={onPress}
      style={[
        styles.card,
        minHeight ? { minHeight } : null,
        isRedBlocked ? { borderColor: color, backgroundColor: `${color}14` } : null,
      ]}
    >
      <View>
        <View style={styles.headerRow}>
          <View style={{ flexShrink: 1 }}>
            <Text style={[styles.name, large ? { fontSize: 22 } : null]}>{displayName}</Text>
            {rawName ? <Text style={styles.rawName}>{rawName}</Text> : null}
          </View>
          <View style={[styles.badge, { backgroundColor: `${color}26` }]}>
            <Text style={[styles.badgeText, large ? { fontSize: 15 } : null, { color }]}>{p.health}</Text>
          </View>
        </View>
        {p.blockedBy ? (
          <Text style={styles.blockedCaption}>{humanizeRawReason(p.blockedBy) ?? 'blocked by an upstream problem.'}</Text>
        ) : null}
        {cause ? (
          <Text style={[styles.causeCaption, large ? { fontSize: 20 } : null, { color: colors.warn }]}>{cause}</Text>
        ) : null}
      </View>
      <View style={styles.metricsRow}>
        <View style={styles.metric}>
          <Text style={styles.metricLabel}>Pending</Text>
          <Text style={[styles.metricValue, large ? { fontSize: 22 } : null, { color: countColor(p.pendingOutbox, colors.textPrimary) }]}>
            {p.pendingOutbox}
          </Text>
        </View>
        {inBus > 0 ? (
          <View style={styles.metric}>
            <Text style={styles.metricLabel}>In bus</Text>
            <Text style={[styles.metricValue, large ? { fontSize: 22 } : null, { color: countColor(inBus, colors.textPrimary) }]}>
              {inBus}
            </Text>
          </View>
        ) : null}
        {lost > 0 ? (
          <View style={styles.metric}>
            <Text style={styles.metricLabel}>Lost</Text>
            <Text style={[styles.metricValue, large ? { fontSize: 22 } : null, { color: colors.bad }]}>{lost}</Text>
          </View>
        ) : null}
        <View style={styles.metric}>
          <Text style={styles.metricLabel}>Failed / Errors</Text>
          <Text
            style={[
              styles.metricValue,
              large ? { fontSize: 22 } : null,
              failedAndErrors > 0 ? { color: colors.bad } : null,
            ]}
          >
            {failedAndErrors}
          </Text>
        </View>
        <View style={styles.metric}>
          <Text style={styles.metricLabel}>Lag</Text>
          <Text style={[styles.metricValue, large ? { fontSize: 22 } : null]}>
            {formatLag(p.sendLagSeconds ?? p.applyLagSeconds)}
          </Text>
        </View>
        <View style={styles.metric}>
          <Text style={styles.metricLabel}>Heartbeat</Text>
          <Text style={[styles.metricValue, large ? { fontSize: 22 } : null]}>
            {formatAgeSeconds(p.heartbeatAgeSeconds)}
          </Text>
        </View>
        <View style={styles.metric}>
          <Text style={styles.metricLabel}>Severity</Text>
          <Text style={[styles.metricValue, large ? { fontSize: 22 } : null]}>S{p.minSeverity}</Text>
        </View>
      </View>
      {stale ? (
        <Text style={[styles.staleNote, large ? { fontSize: 13 } : null]}>{staleMetricsNote(stale, true)}</Text>
      ) : null}
    </Pressable>
  );
}
