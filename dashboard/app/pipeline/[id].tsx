import React, { useMemo } from 'react';
import { ScrollView, Text, View } from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { createThemedStyles } from '../../src/theme/ThemeContext';
import { colors } from '../../src/theme/colors';
import { useConnectionStore } from '../../src/stores/connectionStore';
import { pipelineHealthColor, edgeColor, metricsStaleness, staleMetricsNote } from '../../src/utils/health';
import { formatAgeSeconds, formatHeartbeatAge, formatLag } from '../../src/utils/format';
import { useRollingBuffer } from '../../src/hooks/useRollingBuffer';
import { Sparkline } from '../../src/components/Sparkline';

const useStyles = createThemedStyles((c) => ({
  screen: { flex: 1, backgroundColor: c.background },
  content: { padding: 16, gap: 16 },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  name: { color: c.textPrimary, fontSize: 24, fontWeight: '900' },
  badge: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999 },
  badgeText: { fontWeight: '800' },
  section: { backgroundColor: c.surface, borderRadius: 14, padding: 16, gap: 10 },
  sectionTitle: { color: c.textMuted, fontSize: 12, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.6 },
  metricsGrid: { flexDirection: 'row', flexWrap: 'wrap', gap: 16 },
  metric: { minWidth: 110 },
  metricLabel: { color: c.textMuted, fontSize: 11 },
  metricValue: { color: c.textPrimary, fontSize: 18, fontWeight: '700', marginTop: 2 },
  edgeRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  edgeDot: { width: 12, height: 12, borderRadius: 6 },
  edgeText: { color: c.textSecondary, fontSize: 13 },
  note: { color: c.textMuted, fontSize: 12, fontStyle: 'italic' },
  staleNote: { color: c.textMuted, fontSize: 12, fontStyle: 'italic' },
}));

export default function PipelineDetailScreen() {
  const styles = useStyles();
  const insets = useSafeAreaInsets();
  const { id } = useLocalSearchParams<{ id: string }>();
  // Composite ids like "1001:101" stay strings; Number() would give NaN.
  const pipelineId = decodeURIComponent(String(id ?? ''));

  const pipeline = useConnectionStore((s) => s.pipelines[pipelineId]);
  const edges = useConnectionStore((s) => s.topologyEdges);
  const alertFeed = useConnectionStore((s) => s.alertFeed);

  const heartbeatHistory = useRollingBuffer(pipeline?.heartbeatAgeSeconds ?? null);

  const edge = useMemo(
    () => Object.values(edges).find((e) => String(e.pipelineId) === pipelineId),
    [edges, pipelineId]
  );
  const relatedAlert = useMemo(
    () => alertFeed.find((a) => String(a.pipelineId) === pipelineId && !a.acknowledged),
    [alertFeed, pipelineId]
  );

  if (!pipeline) {
    return (
      <View style={styles.screen}>
        <Text style={{ color: colors.textMuted, padding: 16 }}>Pipeline not found in the current snapshot.</Text>
      </View>
    );
  }

  const color = pipelineHealthColor(pipeline);

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={[
        styles.content,
        { paddingLeft: 16 + insets.left, paddingRight: 16 + insets.right, paddingBottom: 16 + insets.bottom },
      ]}
    >
      <View style={styles.headerRow}>
        <Text style={styles.name}>{pipeline.name}</Text>
        <View style={[styles.badge, { backgroundColor: `${color}26` }]}>
          <Text style={[styles.badgeText, { color }]}>{pipeline.health}</Text>
        </View>
      </View>
      {pipeline.blockedBy ? (
        <Text style={{ color: colors.blocked }}>Blocked by {pipeline.blockedBy}</Text>
      ) : null}

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Metrics</Text>
        {metricsStaleness(pipeline) ? (
          <Text style={styles.staleNote}>{staleMetricsNote(metricsStaleness(pipeline), true)}</Text>
        ) : null}
        <View style={styles.metricsGrid}>
          <View style={styles.metric}>
            <Text style={styles.metricLabel}>Direction</Text>
            <Text style={styles.metricValue}>{pipeline.direction}</Text>
          </View>
          <View style={styles.metric}>
            <Text style={styles.metricLabel}>Pending outbox</Text>
            <Text style={styles.metricValue}>{pipeline.pendingOutbox}</Text>
          </View>
          <View style={styles.metric}>
            <Text style={styles.metricLabel}>Failed outbox</Text>
            <Text style={styles.metricValue}>{pipeline.failedOutbox}</Text>
          </View>
          <View style={styles.metric}>
            <Text style={styles.metricLabel}>Unresolved inbox</Text>
            <Text style={styles.metricValue}>{pipeline.unresolvedInbox}</Text>
          </View>
          <View style={styles.metric}>
            <Text style={styles.metricLabel}>Send lag</Text>
            <Text style={styles.metricValue}>{formatLag(pipeline.sendLagSeconds)}</Text>
          </View>
          <View style={styles.metric}>
            <Text style={styles.metricLabel}>Apply lag</Text>
            <Text style={styles.metricValue}>{formatLag(pipeline.applyLagSeconds)}</Text>
          </View>
          <View style={styles.metric}>
            <Text style={styles.metricLabel}>End-to-end lag</Text>
            <Text style={styles.metricValue}>{formatLag(pipeline.e2eLagSeconds)}</Text>
          </View>
          <View style={styles.metric}>
            <Text style={styles.metricLabel}>Heartbeat age</Text>
            <Text style={styles.metricValue}>{formatAgeSeconds(pipeline.heartbeatAgeSeconds)}</Text>
          </View>
          <View style={styles.metric}>
            <Text style={styles.metricLabel}>Last ping</Text>
            <Text style={styles.metricValue}>
              {metricsStaleness(pipeline) && pipeline.heartbeatAgeSeconds == null
                ? 'not read this poll'
                : formatHeartbeatAge(pipeline.heartbeatAgeSeconds)}
            </Text>
          </View>
          <View style={styles.metric}>
            <Text style={styles.metricLabel}>Severity</Text>
            <Text style={styles.metricValue}>S{pipeline.minSeverity}</Text>
          </View>
        </View>
      </View>

      {edge ? (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Edge to Service Bus ({edge.end})</Text>
          <View style={styles.edgeRow}>
            <View style={[styles.edgeDot, { backgroundColor: edgeColor(edge.level) }]} />
            <Text style={styles.edgeText}>
              {edge.level.toUpperCase()} · pending {edge.pendingCount}
            </Text>
          </View>
          <View style={styles.metricsGrid}>
            <View style={styles.metric}>
              <Text style={styles.metricLabel}>Queued messages</Text>
              <Text style={styles.metricValue}>{edge.queued}</Text>
            </View>
            <View style={styles.metric}>
              <Text style={styles.metricLabel}>Lost messages</Text>
              <Text style={styles.metricValue}>{edge.lost ?? relatedAlert?.lostCount ?? 0}</Text>
            </View>
          </View>
        </View>
      ) : null}

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Heartbeat history (this session)</Text>
        <Sparkline values={heartbeatHistory} color={color} />
      </View>

      <View style={styles.section}>
        <Text style={styles.sectionTitle}>Recent outbox / inbox errors</Text>
        <Text style={styles.note}>
          Full terminal-row history (all Failed/Error rows regardless of age, per PRD §7 item 4)
          is served by the relay once its history endpoint lands -- not yet part of the WS
          snapshot/delta contract in docs/PRD.md §6.
        </Text>
      </View>
    </ScrollView>
  );
}
