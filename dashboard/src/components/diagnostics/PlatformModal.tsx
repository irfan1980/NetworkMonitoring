import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { formatMachineLabel, useMachineLabeler } from '../../utils/machineLabel';
import { Text, View } from 'react-native';
import { createThemedStyles } from '../../theme/ThemeContext';
import { colors } from '../../theme/colors';
import { fetchBuses, fetchPlatformEntities } from '../../services/diagnostics';
import { useDiagnosticsPoll } from '../../hooks/useDiagnosticsPoll';
import { useConnectionStore } from '../../stores/connectionStore';
import { useMachineNodesByPlatform, usePlatformList, usePlatformMachineMap } from '../../utils/platformGrouping';
import {
  busDisplayName,
  pipelineHealthColor,
  metricsStaleness,
  pipelineDisplayName,
  platformMetricsStaleness,
  plainNodeDetail,
  platformHeartbeatAge,
  platformPipelinesWaiting,
  staleMetricsNote,
  withPipelinesWaiting,
} from '../../utils/health';
import { formatAgeSeconds, formatHeartbeatAge, formatTimeAgo } from '../../utils/format';
import { resolveIssues } from '../../utils/configIssues';
import type { BusDiagnostic } from '../../types/diagnostics';
import { DiagnosticsModal } from './DiagnosticsModal';
import { EntityDiagnosticRow } from './EntityDiagnosticRow';

const useStyles = createThemedStyles((c) => ({
  section: { gap: 10, marginBottom: 20 },
  sectionTitle: { color: c.textMuted, fontSize: 13, fontWeight: '800', textTransform: 'uppercase', letterSpacing: 0.6 },
  groupTitle: { color: c.textSecondary, fontSize: 14, fontWeight: '700', marginTop: 6 },
  rows: { gap: 10 },
  centerBox: { paddingVertical: 40, alignItems: 'center', gap: 8 },
  centerText: { color: c.textSecondary, fontSize: 15 },
  errorText: { color: colors.bad, fontSize: 15, fontWeight: '700' },
  pipelineRow: { flexDirection: 'row', alignItems: 'center', gap: 10, backgroundColor: c.surface, borderRadius: 10, padding: 12 },
  pipelineDot: { width: 10, height: 10, borderRadius: 5 },
  pipelineName: { color: c.textPrimary, fontSize: 14, fontWeight: '700', flex: 1 },
  pipelineMeta: { color: c.textMuted, fontSize: 12 },
  staleNote: { color: c.textMuted, fontSize: 12, fontStyle: 'italic' },
  busRow: { flexDirection: 'row', alignItems: 'center', gap: 10, backgroundColor: c.surface, borderRadius: 10, padding: 12 },
  busDot: { width: 10, height: 10, borderRadius: 5 },
  busName: { color: c.textPrimary, fontSize: 14, fontWeight: '700', flex: 1 },
  busTopics: { color: c.textMuted, fontSize: 12 },
  warningBox: { backgroundColor: colors.warnDim, borderRadius: 10, padding: 12 },
  warningText: { color: colors.warn, fontSize: 13, fontWeight: '700' },
  conditionBox: { backgroundColor: c.surface, borderRadius: 10, padding: 12, gap: 4 },
  conditionText: { color: c.textPrimary, fontSize: 14, fontWeight: '700' },
  issueBox: { borderRadius: 10, padding: 12, gap: 4 },
  issueText: { fontSize: 13, fontWeight: '700' },
}));

interface Props {
  visible: boolean;
  platformId: string | null;
  onClose: () => void;
}

export function PlatformModal({ visible, platformId, onClose }: Props) {
  const styles = useStyles();
  const platformList = usePlatformList();
  const topologyNodes = useConnectionStore((s) => s.topologyNodes);
  const pipelines = useConnectionStore((s) => s.pipelines);
  const storeBuses = useConnectionStore((s) => s.buses);
  const platforms = useConnectionStore((s) => s.platforms);
  const pairs = useConnectionStore((s) => s.pairs);
  const machines = useConnectionStore((s) => s.machines);
  const platformMachineMap = usePlatformMachineMap();
  const machineNodesByPlatform = useMachineNodesByPlatform();
  const machineLabeler = useMachineLabeler();

  const labelOf = useMemo(() => {
    const map: Record<string, string> = {};
    for (const p of platformList) map[p.id] = machineLabeler.platformTitle(p.id);
    return map;
  }, [platformList, machineLabeler]);

  const active = visible && !!platformId;
  const fetcher = useCallback(() => fetchPlatformEntities(platformId as string), [platformId]);
  const { data, loading, error } = useDiagnosticsPoll(fetcher, active, platformId ?? '');

  const platformPipelines = useMemo(() => {
    if (!platformId) return [];
    const ids = topologyNodes[platformId]?.pipelines ?? [];
    return ids.map((id) => pipelines[id]).filter(Boolean);
  }, [platformId, topologyNodes, pipelines]);

  const platformStale = useMemo(
    () => (platformId ? platformMetricsStaleness(topologyNodes[platformId], platformPipelines) : null),
    [platformId, topologyNodes, platformPipelines]
  );

  // The platform definition may omit usedBuses; the pairs touching it name the buses its pipelines use.
  const usedBusIds = useMemo(() => {
    const ids = new Set(platformList.find((p) => p.id === platformId)?.usedBuses ?? []);
    for (const p of Object.values(pairs)) if ((p.from === platformId || p.to === platformId) && p.bus) ids.add(p.bus);
    return [...ids];
  }, [platformList, platformId, pairs]);
  const [fallbackBuses, setFallbackBuses] = useState<BusDiagnostic[] | null>(null);
  const storeBusesEmpty = Object.keys(storeBuses).length === 0;
  useEffect(() => {
    if (!active || !storeBusesEmpty) return;
    let cancelled = false;
    fetchBuses()
      .then((list) => {
        if (!cancelled) setFallbackBuses(list);
      })
      .catch(() => {
        if (!cancelled) setFallbackBuses([]);
      });
    return () => {
      cancelled = true;
    };
  }, [active, storeBusesEmpty]);

  const usedBuses = useMemo(() => {
    if (usedBusIds.length === 0) return [];
    if (!storeBusesEmpty) {
      return usedBusIds.map((id) => storeBuses[id]).filter((b): b is NonNullable<typeof b> => !!b);
    }
    return usedBusIds
      .map((id) => fallbackBuses?.find((b) => b.id === id))
      .filter((b): b is BusDiagnostic => !!b);
  }, [usedBusIds, storeBuses, storeBusesEmpty, fallbackBuses]);

  const platformWarnings = useMemo(
    () => Object.values(pairs).filter((p) => (p.from === platformId || p.to === platformId) && p.warning),
    [pairs, platformId]
  );

  const activeAlerts = useConnectionStore((s) => s.activeAlerts);

  const condition = useMemo(() => {
    if (!platformId) return null;
    const node = topologyNodes[platformId];
    const grey = !node || node.dbOk === false || !(node.senderOk && node.receiverOk);
    if (!grey) return null;
    const end =
      !node || (node.senderOk === false) === (node.receiverOk === false)
        ? null
        : node.senderOk === false
          ? 'sender'
          : 'receiver';
    const label = labelOf[platformId] ?? platformId;
    const text = withPipelinesWaiting(
      plainNodeDetail(
        label,
        node?.detail,
        end,
        (end ? topologyNodes[`relay:${platformId}:${end}`]?.heartbeatReadable : undefined) ?? node?.heartbeatReadable
      ),
      platformPipelinesWaiting(platformId, node, topologyNodes, activeAlerts)
    );
    const ages = (['sender', 'receiver'] as const)
      .map((e) => ({ e, age: platformHeartbeatAge(node, e, topologyNodes[`relay:${platformId}:${e}`]) }))
      .filter((x) => x.age != null)
      .map((x) => `${x.e} heartbeat ${formatAgeSeconds(x.age)} ago`);
    return { text, ages: ages.join(' · '), heartbeatUnreadable: node?.heartbeatReadable === false };
  }, [platformId, topologyNodes, activeAlerts, labelOf]);

  const configIssues = useConnectionStore((s) => s.configIssues);
  const platformIssues = useMemo(
    () => resolveIssues(platformId ? topologyNodes[platformId]?.issues : undefined, configIssues),
    [platformId, topologyNodes, configIssues]
  );

  const offlineOwner = useMemo(() => {
    if (!platformId) return null;
    const machineId = platformMachineMap[platformId];
    if (machineId == null) return null;
    const machine = machines[machineId];
    if (!machine || machine.status === 'online') return null;
    const node = machineNodesByPlatform[platformId];
    const lastSeen =
      !node || node.last_known_status === 'unknown' || !node.last_known_ts
        ? 'never observed'
        : `last known ${node.last_known_status} · ${formatTimeAgo(node.last_known_ts)}`;
    return { machineName: formatMachineLabel(machine.machine_name, machine.machine_label_id ?? machine.machine_id), machineStatus: machine.status, lastSeen };
  }, [platformId, platformMachineMap, machines, machineNodesByPlatform]);

  const title = platformId ? labelOf[platformId] ?? platformId : '';

  return (
    <DiagnosticsModal visible={visible} title={title} onClose={onClose}>
      {offlineOwner ? (
        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Status</Text>
          <View style={styles.conditionBox}>
            <Text style={styles.conditionText}>
              {offlineOwner.machineName} is {offlineOwner.machineStatus === 'pending' ? 'not yet online' : 'offline'} --
              this server's current status is not being reported.
            </Text>
            <Text style={styles.pipelineMeta}>{offlineOwner.lastSeen}</Text>
          </View>
        </View>
      ) : null}
      {loading && !data ? (
        <View style={styles.centerBox}>
          <Text style={styles.centerText}>Loading {title}'s pipelines...</Text>
        </View>
      ) : error && !data ? (
        <View style={styles.centerBox}>
          <Text style={styles.errorText}>Could not load this platform's data. {error}</Text>
        </View>
      ) : (
        <>
          {condition && !offlineOwner ? (
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>Status</Text>
              <View style={styles.conditionBox}>
                <Text style={styles.conditionText}>{condition.text}</Text>
                {condition.ages ? <Text style={styles.pipelineMeta}>{condition.ages}</Text> : null}
              </View>
            </View>
          ) : null}

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Pipelines</Text>
            {platformStale ? <Text style={styles.staleNote}>{staleMetricsNote(platformStale, true)}</Text> : null}
            {platformPipelines.length === 0 ? (
              <Text style={styles.centerText}>No pipelines configured for {title}.</Text>
            ) : (
              platformPipelines.map((p) => (
                <View key={p.pipelineId} style={styles.pipelineRow}>
                  <View style={[styles.pipelineDot, { backgroundColor: pipelineHealthColor(p) }]} />
                  <Text style={styles.pipelineName}>{pipelineDisplayName(p, platforms).display}</Text>
                  <Text style={styles.pipelineMeta}>
                    {p.health} · last ping{' '}
                    {condition?.heartbeatUnreadable
                      ? 'cannot be read right now'
                      :
                        (platformStale || metricsStaleness(p)) && p.heartbeatAgeSeconds == null
                        ? 'not read this poll'
                        : formatHeartbeatAge(p.heartbeatAgeSeconds)}
                    {!platformStale && metricsStaleness(p) ? ` · ${staleMetricsNote(metricsStaleness(p))}` : ''}
                  </Text>
                </View>
              ))
            )}
          </View>

          {platformIssues.length > 0 ? (
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>Configuration issues</Text>
              {platformIssues.map((issue) => (
                <View
                  key={issue.code}
                  style={[styles.issueBox, { backgroundColor: issue.severity === 'error' ? colors.badDim : colors.warnDim }]}
                >
                  <Text style={[styles.issueText, { color: issue.severity === 'error' ? colors.bad : colors.warn }]}>
                    {issue.message}
                  </Text>
                </View>
              ))}
            </View>
          ) : null}

          {platformWarnings.length > 0 ? (
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>Warnings</Text>
              {platformWarnings.map((p) => (
                <View key={p.id} style={styles.warningBox}>
                  <Text style={styles.warningText}>
                    {p.warning!.charAt(0).toUpperCase() + p.warning!.slice(1)}
                  </Text>
                </View>
              ))}
            </View>
          ) : null}

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Service buses used</Text>
            {usedBusIds.length === 0 ? (
              <Text style={styles.centerText}>No Service Bus configured for {title}.</Text>
            ) : usedBuses.length === 0 ? (
              <Text style={styles.centerText}>Loading bus status...</Text>
            ) : (
              usedBuses.map((bus) => (
                <View key={bus.id} style={styles.busRow}>
                  <View style={[styles.busDot, { backgroundColor: bus.ok ? colors.good : colors.unknown }]} />
                  <Text style={styles.busName}>{busDisplayName(bus)}</Text>
                  <Text style={styles.busTopics}>{(bus.topics ?? []).join(', ') || 'no topics'}</Text>
                </View>
              ))
            )}
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Outgoing</Text>
            {(data?.outgoing ?? []).every((g) => (g.entities?.length ?? 0) === 0) ? (
              <Text style={styles.centerText}>Nothing configured to send out from {title}.</Text>
            ) : (
              (data?.outgoing ?? []).map((group) =>
                (group.entities?.length ?? 0) === 0 ? null : (
                  <View key={`out-${group.to}`} style={styles.rows}>
                    <Text style={styles.groupTitle}>To {labelOf[group.to] ?? group.to}</Text>
                    {(group.entities ?? []).map((entity) => (
                      <EntityDiagnosticRow
                        key={`${group.to}-${entity.entity}`}
                        entity={entity}
                        windowSeconds={3600}
                        fromLabel={title}
                        toLabel={labelOf[group.to] ?? group.to}
                      />
                    ))}
                  </View>
                )
              )
            )}
          </View>

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Incoming</Text>
            {(data?.incoming ?? []).every((g) => (g.entities?.length ?? 0) === 0) ? (
              <Text style={styles.centerText}>Nothing configured to arrive at {title}.</Text>
            ) : (
              (data?.incoming ?? []).map((group) =>
                (group.entities?.length ?? 0) === 0 ? null : (
                  <View key={`in-${group.from}`} style={styles.rows}>
                    <Text style={styles.groupTitle}>From {labelOf[group.from] ?? group.from}</Text>
                    {(group.entities ?? []).map((entity) => (
                      <EntityDiagnosticRow
                        key={`${group.from}-${entity.entity}`}
                        entity={entity}
                        windowSeconds={3600}
                        fromLabel={labelOf[group.from] ?? group.from}
                        toLabel={title}
                      />
                    ))}
                  </View>
                )
              )
            )}
          </View>
        </>
      )}
    </DiagnosticsModal>
  );
}
