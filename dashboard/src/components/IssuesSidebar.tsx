import React, { useMemo, useState } from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { createThemedStyles } from '../theme/ThemeContext';
import { colors } from '../theme/colors';
import { useConnectionStore } from '../stores/connectionStore';
import { useResponsive } from '../hooks/useResponsive';
import { usePlatformList } from '../utils/platformGrouping';
import { useMachineLabeler } from '../utils/machineLabel';
import {
  alertPipelinesWaiting,
  busDisplayName,
  isRelayOrDatabaseAlert,
  pipelinesForPlatform,
  plainNodeDetail,
  platformMetricsStaleness,
  platformPipelinesWaiting,
  staleMetricsNote,
  sanitizeBusNamesInText,
  withPipelinesWaiting,
  type OverallStatus,
} from '../utils/health';
import { groupAlerts, sortGroupsBySeverityThenRecency } from '../utils/alertGrouping';
import { countColor, queueLevelForCount, type QueueLevel } from '../constants/queueThresholds';
import { formatTimeAgo } from '../utils/format';
import type { AlertMessage, MonitoredObject, Pair, Pipeline, StandaloneServiceNode, TopologyNode } from '../types/relay';
import { BusModal } from './diagnostics/BusModal';
import { PlatformModal } from './diagnostics/PlatformModal';
import { AlertDetailModal } from './diagnostics/AlertDetailModal';
import { MachineModal } from './diagnostics/MachineModal';

export const SIDEBAR_WIDTH = 220;

const useStyles = createThemedStyles((c) => ({
  wall: {
    flex: 1,
    width: SIDEBAR_WIDTH,
    alignSelf: 'stretch',
    borderLeftWidth: 1,
    borderLeftColor: c.border,
    paddingLeft: 16,
    gap: 12,
    overflow: 'hidden',
  },
  phone: { gap: 8 },
  headline: { fontSize: 20, fontWeight: '900' },
  reason: { fontSize: 13, color: c.textSecondary },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    alignSelf: 'flex-start',
    borderRadius: 999,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderWidth: 1,
  },
  chipText: { fontSize: 13, fontWeight: '800' },
  list: { gap: 6 },
  scroll: { flex: 1, minHeight: 0 },
  row: { flexDirection: 'row', gap: 8, alignItems: 'flex-start', paddingVertical: 6 },
  dot: { width: 8, height: 8, borderRadius: 4, marginTop: 6 },
  rowTextWrap: { flex: 1, flexWrap: 'wrap' },
  rowPrefix: { color: c.textMuted, fontSize: 11, fontWeight: '800' },
  rowText: { color: c.textPrimary, fontSize: 13 },
  rowSince: { color: c.textMuted, fontSize: 11, fontWeight: '700' },
  rowTextMuted: { color: c.textMuted, fontSize: 12, fontStyle: 'italic' },
}));

const ALERT_LINES_CAP = 15;

interface Line {
  key: string;
  prefix: string;
  text: string;
  severity: 'error' | 'warning';
  onPress?: () => void;
  dotColor?: string;
  muted?: boolean;
  since?: string | null;
}

function alertBacklogCount(alert: AlertMessage, pipeline: Pipeline | undefined): number {
  return Math.max(
    alert.queuedCount ?? 0,
    alert.lostCount ?? 0,
    pipeline?.pendingOutbox ?? 0,
    pipeline?.pendingInbox ?? 0
  );
}

function pendingBandName(level: QueueLevel): string | null {
  switch (level) {
    case 'red':
      return 'Red';
    case 'degraded':
      return 'Degraded';
    case 'yellow':
      return 'Yellow';
    default:
      return null;
  }
}

function pendingClauseFor(count: number | null | undefined): string | null {
  if (count == null) return null;
  const band = pendingBandName(queueLevelForCount(count));
  return band ? `${count} pending (${band})` : null;
}

function backlogDotColor(pairsTouching: Pair[]): string {
  if (pairsTouching.some((p) => p.status === 'red')) return colors.bad;
  if (pairsTouching.some((p) => p.status === 'yellow')) return colors.warn;
  return colors.unknown;
}

function earliestSince(values: (string | null | undefined)[]): string | null {
  let best: { iso: string; t: number } | null = null;
  for (const iso of values) {
    if (!iso) continue;
    const t = Date.parse(iso);
    if (!Number.isFinite(t)) continue;
    if (!best || t < best.t) best = { iso, t };
  }
  return best?.iso ?? null;
}

function platformConditionSince(
  platformId: string,
  node: TopologyNode | undefined,
  objects: Record<string, MonitoredObject>,
  topologyNodes: Record<string, TopologyNode>,
  end: 'sender' | 'receiver' | null
): string | null {
  if (node?.conditionSince) return node.conditionSince;

  if (node?.dbOk === false) {
    const failing = ['tcp', 'auth', 'query']
      .map((k) => objects[`db:${platformId}:${k}`])
      .filter((o) => o && o.status !== 'ok');
    const fromObjects = earliestSince(failing.map((o) => o?.since));
    if (fromObjects) return fromObjects;
    return node?.since ?? null;
  }

  const ends: ('sender' | 'receiver')[] =
    end != null ? [end] : ([node?.senderOk === false ? 'sender' : null, node?.receiverOk === false ? 'receiver' : null].filter(Boolean) as ('sender' | 'receiver')[]);
  const fromRelays = earliestSince(ends.map((e) => topologyNodes[`relay:${platformId}:${e}`]?.since));
  if (fromRelays) return fromRelays;

  return node?.since ?? null;
}

function busConditionSince(busId: string, busSince: string | null | undefined, objects: Record<string, MonitoredObject>): string | null {
  if (busSince) return busSince;
  const failing = ['health', 'amqp'].map((k) => objects[`bus:${busId}:${k}`]).filter((o) => o && o.status !== 'ok');
  return earliestSince(failing.map((o) => o?.since));
}

interface Props {
  status: OverallStatus;
  onLongPressTitle?: () => void;
}

function serviceForAlert(alert: AlertMessage, services: StandaloneServiceNode[]): StandaloneServiceNode | undefined {
  if (alert.machine_id != null) return undefined;
  const key = alert.server_id != null ? String(alert.server_id) : /(?:^|:)db:(?:[a-z]+:)?(\d+)/.exec(alert.rootCause ?? '')?.[1];
  if (!key) return undefined;
  return services.find((sv) => String(sv.server_id) === key);
}

function configEntityLabel(id: string): string {
  const m = /^settings\.([^.]+)\./.exec(id);
  return m ? `Relay config \u00b7 ${m[1]}` : id;
}

export function IssuesSidebar({ status, onLongPressTitle }: Props) {
  const styles = useStyles();
  const router = useRouter();
  const { isPhone, height: winHeight } = useResponsive();
  const [expanded, setExpanded] = useState(false);
  const [alertsExpanded, setAlertsExpanded] = useState(false);

  const platformList = usePlatformList();
  const estateServices = useConnectionStore((s) => s.estateServices);
  const machineLabeler = useMachineLabeler();
  const topologyNodes = useConnectionStore((s) => s.topologyNodes);
  const objects = useConnectionStore((s) => s.objects);
  const pairs = useConnectionStore((s) => s.pairs);
  const bus = useConnectionStore((s) => s.bus);
  const buses = useConnectionStore((s) => s.buses);
  const configIssues = useConnectionStore((s) => s.configIssues);
  const activeAlerts = useConnectionStore((s) => s.activeAlerts);
  const pipelines = useConnectionStore((s) => s.pipelines);
  const schedules = useConnectionStore((s) => s.schedules);
  const machines = useConnectionStore((s) => s.machines);

  const [machineModalFor, setMachineModalFor] = useState<number | null>(null);
  const [platformModalFor, setPlatformModalFor] = useState<string | null>(null);
  const [busModalPair, setBusModalPair] = useState<{ from: string; to: string } | null>(null);
  const [alertModalId, setAlertModalId] = useState<string | null>(null);

  const labelOf = useMemo(() => {
    const map: Record<string, string> = {};
    for (const p of platformList) map[p.id] = p.label;
    for (const sv of estateServices) map[String(sv.server_id)] = sv.node_name;
    return (id: string) => map[id] ?? id;
  }, [platformList, estateServices]);

  const firstPairForBus = useMemo(() => {
    const map: Record<string, Pair | undefined> = {};
    for (const pair of Object.values(pairs)) {
      if (pair.bus && !map[pair.bus]) map[pair.bus] = pair;
    }
    return map;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pairs]);

  const busOnPress = (busId: string): (() => void) | undefined => {
    const rep = firstPairForBus[busId];
    return rep ? () => setBusModalPair({ from: rep.from, to: rep.to }) : undefined;
  };

  const lines = useMemo(() => {
    const out: Line[] = [];

    for (const p of platformList) {
      const node = topologyNodes[p.id];
      const grey = !node || node.dbOk === false || !(node.senderOk && node.receiverOk);
      if (!grey) continue;
      const end = !node || (node.senderOk === false) === (node.receiverOk === false) ? null : node.senderOk === false ? 'sender' : 'receiver';
      const touching = Object.values(pairs).filter((pr) => pr.from === p.id || pr.to === p.id);
      const dbDown = node?.dbOk === false;
      const endsDown: Array<'sender' | 'receiver'> = dbDown ? [] : ([node?.senderOk === false ? 'sender' : null, node?.receiverOk === false ? 'receiver' : null].filter(Boolean) as Array<'sender' | 'receiver'>);
      const pendingByEnd = endsDown.map((e) => ({
        end: e,
        count: e === 'sender' ? node?.senderPendingCount ?? null : node?.receiverPendingCount ?? null,
      }));
      const worstPending = pendingByEnd.reduce<{ end: 'sender' | 'receiver'; count: number } | null>((best, cur) => {
        if (cur.count == null) return best;
        if (!best || cur.count > best.count) return { end: cur.end, count: cur.count };
        return best;
      }, null);
      const pendingClauses = pendingByEnd
        .map((p2) => pendingClauseFor(p2.count))
        .filter((c): c is string => !!c);
      out.push({
        key: `platform:${p.id}`,
        prefix: machineLabeler.platform(p.id, `${p.id} | ${p.label}`),
        text: [
          withPipelinesWaiting(
            plainNodeDetail(
              p.label,
              node?.detail,
              end,
              (end ? topologyNodes[`relay:${p.id}:${end}`]?.heartbeatReadable : undefined) ?? node?.heartbeatReadable
            ),
            platformPipelinesWaiting(p.id, node, topologyNodes, activeAlerts)
          ),
          ...pendingClauses,
        ].join(' -- '),
        severity: 'error',
        dotColor: dbDown ? colors.bad : worstPending ? countColor(worstPending.count, colors.unknown) : backlogDotColor(touching),
        since: platformConditionSince(p.id, node, objects, topologyNodes, end),
        onPress: () => setPlatformModalFor(p.id),
      });
    }

    for (const p of platformList) {
      const stale = platformMetricsStaleness(topologyNodes[p.id], pipelinesForPlatform(p.id, topologyNodes[p.id], pipelines));
      if (!stale) continue;
      out.push({
        key: `stale:${p.id}`,
        prefix: machineLabeler.platform(p.id, `${p.id} | ${p.label}`),
        text: `${staleMetricsNote(stale, true)} -- still shown, just not this second's.`,
        severity: 'warning',
        muted: true,
        onPress: () => setPlatformModalFor(p.id),
      });
    }

    const busList = Object.values(buses);
    if (busList.length > 0) {
      for (const b of busList) {
        if (b.ok !== false) continue;
        const touching = Object.values(pairs).filter((pr) => pr.bus === b.id);
        out.push({
          key: `bus:${b.id}`,
          prefix: `${b.id} | ${busDisplayName(b)}`,
          text: `${busDisplayName(b)} is not answering.`,
          severity: 'error',
          dotColor: backlogDotColor(touching),
          since: busConditionSince(b.id, b.since, objects),
          onPress: busOnPress(b.id),
        });
      }
    } else if (bus?.ok === false) {
      out.push({
        key: 'bus:unknown',
        prefix: 'Service Bus',
        text: 'Service Bus is not answering.',
        severity: 'error',
        dotColor: backlogDotColor(Object.values(pairs)),
      });
    }

    for (const pair of Object.values(pairs)) {
      if (!pair.warning) continue;
      out.push({
        key: `pair-warning:${pair.id}`,
        prefix: machineLabeler.pair(pair.from, pair.to, `${pair.id} | ${labelOf(pair.from)} -> ${labelOf(pair.to)}`),
        text: pair.warning.charAt(0).toUpperCase() + pair.warning.slice(1),
        severity: 'warning',
        dotColor: backlogDotColor([pair]),
        since: pair.since ?? null,
        onPress: () => setBusModalPair({ from: pair.from, to: pair.to }),
      });
    }

    for (const pair of Object.values(pairs)) {
      if (pair.missingEnd && pair.missingEndReason === 'unconfigured') {
        const missingIsSender = pair.missingEnd === 'sender';
        out.push({
          key: `pair-unconfigured:${pair.id}`,
          prefix: machineLabeler.pair(pair.from, pair.to, `${pair.id} | ${labelOf(pair.from)} -> ${labelOf(pair.to)}`),
          text: `${missingIsSender ? 'Sender' : 'Receiver'} half not configured (${labelOf(missingIsSender ? pair.from : pair.to)}).`,
          severity: 'warning',
          muted: true,
          since: pair.since ?? null,
          onPress: () => setBusModalPair({ from: pair.from, to: pair.to }),
        });
        continue;
      }
      if (pair.missingEndReason !== 'disabled') continue;
      const ownerPlatform = pair.missingEnd === 'sender' ? pair.from : pair.to;
      const otherPlatform = pair.missingEnd === 'sender' ? pair.to : pair.from;
      out.push({
        key: `pair-disabled:${pair.id}`,
        prefix: machineLabeler.pair(pair.from, pair.to, `${pair.id} | ${labelOf(pair.from)} -> ${labelOf(pair.to)}`),
        text:
          pair.missingEnd === 'sender'
            ? `${labelOf(ownerPlatform)}'s sending pipeline to ${labelOf(otherPlatform)} is disabled.`
            : `${labelOf(ownerPlatform)}'s receiving pipeline from ${labelOf(otherPlatform)} is disabled.`,
        severity: 'warning',
        muted: true,
        since: pair.since ?? null,
        onPress: () => setBusModalPair({ from: pair.from, to: pair.to }),
      });
    }

    for (const issue of configIssues) {
      const where = issue.where;
      const id = where?.serverId ?? where?.busId ?? (where?.pipelineId != null ? String(where.pipelineId) : where?.entity) ?? issue.code;
      const name = where?.serverId
        ? labelOf(where.serverId)
        : where?.busId
          ? busDisplayName(buses[where.busId], where.busId)
          : where?.entity ?? '';
      const onPress = where?.serverId
        ? () => setPlatformModalFor(where.serverId!)
        : where?.busId
          ? busOnPress(where.busId)
          : undefined;
      const downServer = where?.serverId ? topologyNodes[where.serverId] : undefined;
      const serverNotOk = !!where?.serverId && (!downServer || downServer.dbOk === false || !(downServer.senderOk && downServer.receiverOk));
      const busNotOk = !!where?.busId && buses[where.busId]?.ok === false;
      const touchingIssue = serverNotOk
        ? Object.values(pairs).filter((pr) => pr.from === where!.serverId || pr.to === where!.serverId)
        : busNotOk
          ? Object.values(pairs).filter((pr) => pr.bus === where!.busId)
          : null;
      const machineName = where?.machineId
        ? Object.values(machines).find((m) => m.machine_label_id === where.machineId)?.machine_name
        : undefined;
      const joined = where?.machineId
        ? `Machine ${machineName ? `${machineName} (${where.machineId})` : where.machineId}`
        : !name || name === id ? configEntityLabel(id) : `${id} | ${name}`;
      out.push({
        key: `config:${issue.code}:${id}:${issue.message}`,
        prefix: where?.serverId ? machineLabeler.platform(where.serverId, joined) : joined,
        text: issue.message,
        severity: issue.severity,
        dotColor: touchingIssue ? backlogDotColor(touchingIssue) : undefined,
        onPress,
      });
    }

    const stillActive = Object.values(activeAlerts).filter((a) => a.event !== 'resolved' && !a.resolvedAt);
    // A broken independent service shows on the map from node status; list it even when no alert has been raised for it.
    for (const sv of estateServices) {
      if (sv.status !== 'down') continue;
      if (stillActive.some((a) => serviceForAlert(a, estateServices)?.node_id === sv.node_id)) continue;
      out.push({
        key: `service:${sv.node_id}`,
        prefix: `${sv.node_name} · no machine`,
        text: `${sv.node_type} ${sv.node_platform} is not answering.`,
        severity: 'error',
        dotColor: colors.bad,
      });
    }
    const groups = sortGroupsBySeverityThenRecency(groupAlerts(stillActive));
    const visibleGroups = alertsExpanded ? groups : groups.slice(0, ALERT_LINES_CAP);
    for (const g of visibleGroups) {
      const alert = g.alert;
      if (alert.rootCause === 'schedule' || alert.kind === 'schedule') {
        const scheduleId = alert.schedule_id ?? Number(alert.alertId.split(':')[1]);
        const sc = schedules[String(scheduleId)];
        out.push({
          key: `alert:${alert.alertId}`,
          prefix: `#${scheduleId}${sc ? ` ${sc.name}` : ''}`,
          text: alert.reason,
          severity: 'error',
          dotColor: colors.bad,
          since: alert.since ?? null,
          onPress: Number.isFinite(scheduleId) ? () => router.push(`/scheduler/${scheduleId}`) : () => setAlertModalId(alert.alertId),
        });
        continue;
      }
      if (alert.rootCause === 'scheduler_endpoint' || alert.kind === 'scheduler_endpoint') {
        const machineId = alert.machine_id ?? Number(alert.alertId.split(':')[1]);
        const info = machines[machineId];
        out.push({
          key: `alert:${alert.alertId}`,
          prefix: info ? `${machineLabeler.machine(machineId) ?? `Machine ${machineId}`} scheduler` : 'Scheduler',
          text: alert.reason,
          severity: 'warning',
          dotColor: colors.unknown,
          since: alert.since ?? null,
          onPress: info ? () => setMachineModalFor(machineId) : () => setAlertModalId(alert.alertId),
        });
        continue;
      }
      const service = serviceForAlert(alert, estateServices);
      const pipeline = alert.pipelineId != null ? pipelines[String(alert.pipelineId)] : undefined;
      const displayCount = alert.failedCount ?? g.count;
      const countSuffix = displayCount > 1 ? ` (×${displayCount})` : '';
      out.push({
        key: `alert:${alert.alertId}`,
        prefix: service
          ? `${service.node_name} · no machine`
          : alert.pipelineId != null ? `${alert.pipelineId} | ${pipeline?.name ?? String(alert.pipelineId)}` : 'Alert',
        text: `${
          isRelayOrDatabaseAlert(alert)
            ? withPipelinesWaiting(sanitizeBusNamesInText(alert.reason), alertPipelinesWaiting(alert))
            : sanitizeBusNamesInText(alert.reason)
        }${countSuffix}`,
        severity: alert.severity <= 2 ? 'error' : 'warning',
        dotColor: countColor(alertBacklogCount(alert, pipeline), colors.unknown),
        since: alert.since ?? null,
        onPress: () => setAlertModalId(alert.alertId),
      });
    }
    const hiddenAlerts = groups.slice(visibleGroups.length).reduce((sum, g) => sum + g.count, 0);
    if (hiddenAlerts > 0) {
      out.push({
        key: 'alerts-more',
        prefix: '',
        text: `+${hiddenAlerts} more alert${hiddenAlerts === 1 ? '' : 's'}`,
        severity: 'warning',
        onPress: () => setAlertsExpanded(true),
      });
    }

    return out;
  }, [platformList, topologyNodes, objects, bus, buses, pairs, configIssues, activeAlerts, pipelines, schedules, machines, estateServices, router, labelOf, machineLabeler, firstPairForBus, alertsExpanded]);

  const modals = (
    <>
      <PlatformModal visible={!!platformModalFor} platformId={platformModalFor} onClose={() => setPlatformModalFor(null)} />
      <BusModal
        visible={!!busModalPair}
        from={busModalPair?.from ?? null}
        to={busModalPair?.to ?? null}
        fromLabel={busModalPair ? machineLabeler.platformTitle(busModalPair.from) : ''}
        toLabel={busModalPair ? machineLabeler.platformTitle(busModalPair.to) : ''}
        onClose={() => setBusModalPair(null)}
      />
      <AlertDetailModal alertId={alertModalId} onClose={() => setAlertModalId(null)} />
      <MachineModal visible={machineModalFor != null} machineId={machineModalFor} onClose={() => setMachineModalFor(null)} />
    </>
  );

  const list = (
    <View style={styles.list}>
      {lines.map((line) => (
        <Pressable key={line.key} style={styles.row} onPress={line.onPress} disabled={!line.onPress}>
          <View
            style={[
              styles.dot,
              { backgroundColor: line.muted ? colors.textMuted : line.dotColor ?? (line.severity === 'error' ? colors.bad : colors.warn) },
            ]}
          />
          <View style={styles.rowTextWrap}>
            <Text style={styles.rowPrefix}>
              {line.prefix}
              {line.since ? <Text style={styles.rowSince}>{line.prefix ? '  ' : ''}{formatTimeAgo(line.since)}</Text> : null}
            </Text>
            <Text style={[styles.rowText, line.muted ? styles.rowTextMuted : null]}>{line.text}</Text>
          </View>
        </Pressable>
      ))}
    </View>
  );

  const issueCount = lines.filter((l) => !l.muted).length;
  const noteCount = lines.length - issueCount;
  const chipLabel =
    issueCount > 0
      ? `${issueCount} issue${issueCount === 1 ? '' : 's'}${noteCount > 0 ? ` · ${noteCount} note${noteCount === 1 ? '' : 's'}` : ''}`
      : `${noteCount} note${noteCount === 1 ? '' : 's'}`;

  if (isPhone) {
    const maxListHeight = Math.round(winHeight * 0.4);
    return (
      <View style={styles.phone}>
        <Pressable onLongPress={onLongPressTitle} delayLongPress={900}>
          <Text style={[styles.headline, { color: status.color }]}>{status.title}</Text>
          <Text style={styles.reason} numberOfLines={2}>{status.reason}</Text>
          {status.since ? <Text style={styles.rowSince}>{formatTimeAgo(status.since)}</Text> : null}
        </Pressable>
        {lines.length > 0 ? (
          <>
            <Pressable style={[styles.chip, { backgroundColor: colors.surfaceRaised, borderColor: colors.border }]} onPress={() => setExpanded((e) => !e)}>
              <Text style={[styles.chipText, { color: colors.textPrimary }]}>
                {chipLabel} {expanded ? '▲' : '▼'}
              </Text>
            </Pressable>
            {expanded ? (
              <ScrollView style={[styles.scroll, { maxHeight: maxListHeight }]} nestedScrollEnabled>
                {list}
              </ScrollView>
            ) : null}
          </>
        ) : null}
        {modals}
      </View>
    );
  }

  return (
    <View style={styles.wall}>
      <Pressable onLongPress={onLongPressTitle} delayLongPress={900}>
        <Text style={[styles.headline, { color: status.color }]}>{status.title}</Text>
        <Text style={styles.reason} numberOfLines={2}>{status.reason}</Text>
        {status.since ? <Text style={styles.rowSince}>{formatTimeAgo(status.since)}</Text> : null}
      </Pressable>
      {lines.length > 0 ? <ScrollView style={styles.scroll}>{list}</ScrollView> : null}
      {modals}
    </View>
  );
}
