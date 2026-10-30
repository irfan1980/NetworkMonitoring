import React, { useMemo } from 'react';
import { formatMachineLabel } from '../../utils/machineLabel';
import { Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { createThemedStyles } from '../../theme/ThemeContext';
import { colors } from '../../theme/colors';
import { useConnectionStore } from '../../stores/connectionStore';
import { usePlatformList, usePlatformMachineMap } from '../../utils/platformGrouping';
import { formatTimeAgo } from '../../utils/format';
import { busDisplayName } from '../../utils/health';
import type { MachineNode, Pair } from '../../types/relay';
import { usePinSession } from '../../hooks/usePinSession';
import { schedulerStateColor, schedulerStateLabel, sortSchedules } from '../../utils/schedules';
import { ScheduleRow } from '../scheduler/ScheduleRow';
import { StopRunButton } from '../scheduler/StopRunButton';
import { TestRunButton } from '../scheduler/TestRunButton';
import { DiagnosticsModal } from './DiagnosticsModal';

const useStyles = createThemedStyles((c) => ({
  section: { gap: 10, marginBottom: 20 },
  sectionTitle: { color: c.textMuted, fontSize: 13, fontWeight: '800', textTransform: 'uppercase', letterSpacing: 0.6 },
  centerText: { color: c.textSecondary, fontSize: 15 },
  identityBox: { backgroundColor: c.surface, borderRadius: 10, padding: 12, gap: 6 },
  identityRow: { flexDirection: 'row', justifyContent: 'space-between', gap: 12 },
  identityLabel: { color: c.textMuted, fontSize: 12 },
  identityValue: { color: c.textPrimary, fontSize: 13, fontWeight: '700' },
  nodeCard: { backgroundColor: c.surface, borderRadius: 10, padding: 12, gap: 8, marginBottom: 10 },
  nodeHeaderRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  nodeDot: { width: 10, height: 10, borderRadius: 5 },
  nodeName: { color: c.textPrimary, fontSize: 14, fontWeight: '800', flex: 1 },
  nodeMeta: { color: c.textMuted, fontSize: 12 },
  nodeStatusLine: { color: c.textSecondary, fontSize: 13, fontWeight: '700' },
  lastSeenLine: { color: c.textMuted, fontSize: 12 },
  connRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 6, borderTopWidth: 1, borderTopColor: c.border },
  connText: { color: c.textSecondary, fontSize: 12, flex: 1 },
  crossBadge: { backgroundColor: colors.blockedDim, borderRadius: 6, paddingHorizontal: 6, paddingVertical: 2 },
  crossBadgeText: { color: colors.accent, fontSize: 10, fontWeight: '800' },
  schedulerLine: { fontSize: 13, fontWeight: '700' },
  schedulerReason: { color: c.textMuted, fontSize: 12, fontStyle: 'italic' },
  offlineNote: { color: c.textMuted, fontSize: 12, fontStyle: 'italic', marginTop: -2, marginBottom: 6 },
}));

interface Props {
  visible: boolean;
  machineId: number | null;
  onClose: () => void;
  onSelectPlatform?: (platformId: string) => void;
}

function nodeStatusText(node: MachineNode, machineOnline: boolean): { text: string; dotColor: string } {
  if (!machineOnline) {
    return { text: 'Unknown -- not currently reporting', dotColor: colors.unknown };
  }
  switch (node.status) {
    case 'ok':
      return { text: 'Confirmed healthy', dotColor: colors.good };
    case 'down':
      return { text: 'Confirmed broken (down)', dotColor: colors.bad };
    case 'unread':
      return { text: 'Unknown -- not read this poll', dotColor: colors.unknown };
    default:
      return { text: 'Unknown -- not currently reporting', dotColor: colors.unknown };
  }
}

function lastSeenText(node: MachineNode): string {
  if (node.last_known_status === 'unknown' || !node.last_known_ts) return 'Never observed';
  return `Last known ${node.last_known_status} · ${formatTimeAgo(node.last_known_ts)}`;
}

export function MachineModal({ visible, machineId, onClose, onSelectPlatform }: Props) {
  const styles = useStyles();
  const machine = useConnectionStore((s) => (machineId != null ? s.machines[machineId] : undefined));
  const machineSocketState = useConnectionStore((s) => (machineId != null ? s.machineSocketState[machineId] : undefined));
  const pairs = useConnectionStore((s) => s.pairs);
  const buses = useConnectionStore((s) => s.buses);
  const machines = useConnectionStore((s) => s.machines);
  const allSchedules = useConnectionStore((s) => s.schedules);
  const router = useRouter();
  const pin = usePinSession();
  const platformList = usePlatformList();
  const platformMachineMap = usePlatformMachineMap();

  const labelOf = useMemo(() => {
    const map: Record<string, string> = {};
    for (const p of platformList) map[p.id] = p.label;
    return map;
  }, [platformList]);

  const title = (machine ? formatMachineLabel(machine.machine_name, machine.machine_label_id ?? machine.machine_id) : null) ?? (machineId != null ? `Machine ${machineId}` : '');
  const online = machine?.status === 'online';

  const nodes = machine?.nodes ?? [];

  const machineSchedules = useMemo(
    () => sortSchedules(Object.values(allSchedules).filter((sc) => sc.machine_id === machineId)),
    [allSchedules, machineId]
  );

  const connectionsByNode = useMemo(() => {
    const nodeIds = new Set(nodes.map((n) => String(n.server_id)));
    const map: Record<string, Pair[]> = {};
    for (const pair of Object.values(pairs)) {
      if (nodeIds.has(pair.from)) (map[pair.from] ??= []).push(pair);
      if (nodeIds.has(pair.to)) (map[pair.to] ??= []).push(pair);
    }
    return map;
  }, [nodes, pairs]);

  function counterpartMachineName(platformId: string, ownMachineId: number | null): string | null {
    const otherMachineId = platformMachineMap[platformId];
    if (otherMachineId == null || otherMachineId === ownMachineId) return null;
    const other = machines[otherMachineId];
    return other ? formatMachineLabel(other.machine_name, other.machine_label_id ?? otherMachineId) : `Machine ${otherMachineId}`;
  }

  return (
    <>
    <DiagnosticsModal visible={visible} title={title} onClose={onClose}>
      {!machine ? (
        <Text style={styles.centerText}>Machine {machineId} is not in this session's inventory.</Text>
      ) : (
        <>
          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Machine</Text>
            <View style={styles.identityBox}>
              <View style={styles.identityRow}>
                <Text style={styles.identityLabel}>ID</Text>
                <Text style={styles.identityValue}>{machine.machine_label_id ?? machine.machine_id}</Text>
              </View>
              <View style={styles.identityRow}>
                <Text style={styles.identityLabel}>IP</Text>
                <Text style={styles.identityValue}>{machine.machine_ip}</Text>
              </View>
              <View style={styles.identityRow}>
                <Text style={styles.identityLabel}>Type / OS</Text>
                <Text style={styles.identityValue}>
                  {machine.machine_type} / {machine.machine_os}
                </Text>
              </View>
              <View style={styles.identityRow}>
                <Text style={styles.identityLabel}>Status</Text>
                <Text style={styles.identityValue}>
                  {online
                    ? machineSocketState === 'live'
                      ? `Online${machine.ping?.rtt_ms != null ? ` · ping ${Math.round(machine.ping.rtt_ms)}ms` : ''}`
                      : 'Online · connecting…'
                    : machine.status === 'pending'
                      ? 'Registered, never seen online'
                      : 'Offline'}
                </Text>
              </View>
              <View style={styles.identityRow}>
                <Text style={styles.identityLabel}>Last active</Text>
                <Text style={styles.identityValue}>
                  {machine.last_active_ts ? formatTimeAgo(machine.last_active_ts) : 'Never'}
                </Text>
              </View>
            </View>
          </View>

          {!online ? (
            <Text style={styles.offlineNote}>
              This machine is not currently online -- everything below is last-known/derived, not live.
            </Text>
          ) : null}

          {machine.machine_scheduler ? (
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>Scheduler</Text>
              <Text style={[styles.schedulerLine, { color: schedulerStateColor(machine.scheduler_state) }]}>
                {schedulerStateLabel(machine.scheduler_state)}
              </Text>
              {machine.scheduler_reason ? <Text style={styles.schedulerReason}>{machine.scheduler_reason}</Text> : null}
            </View>
          ) : null}

          {machine.machine_scheduler ? (
            <View style={styles.section}>
              <Text style={styles.sectionTitle}>Schedules ({machineSchedules.length})</Text>
              {machineSchedules.length === 0 ? (
                <Text style={styles.centerText}>No schedules on this machine.</Text>
              ) : (
                machineSchedules.map((sc) => (
                  <ScheduleRow
                    key={sc.id}
                    schedule={sc}
                    showMachine={false}
                    onPress={() => {
                      onClose();
                      router.push(`/scheduler/${sc.id}`);
                    }}
                    footer={
                      <>
                        <TestRunButton scheduleId={sc.id} pin={pin} />
                        {sc.running ? <StopRunButton scheduleId={sc.id} pin={pin} /> : null}
                      </>
                    }
                  />
                ))
              )}
            </View>
          ) : null}

          <View style={styles.section}>
            <Text style={styles.sectionTitle}>Services ({nodes.length})</Text>
            {nodes.length === 0 ? (
              <Text style={styles.centerText}>No services on this machine (scheduler-only or online watch).</Text>
            ) : (
              nodes.map((node) => {
                const { text: statusText, dotColor } = nodeStatusText(node, online);
                const nodeConnections = connectionsByNode[String(node.server_id)] ?? [];
                return (
                  <View key={node.node_id} style={styles.nodeCard}>
                    <View style={styles.nodeHeaderRow}>
                      <View style={[styles.nodeDot, { backgroundColor: dotColor }]} />
                      <Text style={styles.nodeName}>{node.node_name}</Text>
                      <Text style={styles.nodeMeta}>
                        {node.node_type} · {node.node_platform} · id {node.server_id}
                      </Text>
                    </View>
                    <Text style={styles.nodeStatusLine}>{statusText}</Text>
                    <Text style={styles.lastSeenLine}>{lastSeenText(node)}</Text>

                    {nodeConnections.length > 0 ? (
                      <View>
                        {nodeConnections.map((pair) => {
                          const isOutgoing = pair.from === String(node.server_id);
                          const counterpartId = isOutgoing ? pair.to : pair.from;
                          const counterpartLabel = labelOf[counterpartId] ?? counterpartId;
                          const crossName =
                            counterpartMachineName(counterpartId, machine.machine_id) ??
                            (pair.cross_machine && machines[pair.cross_machine.counterpart_machine_id]
                              ? formatMachineLabel(machines[pair.cross_machine.counterpart_machine_id].machine_name, machines[pair.cross_machine.counterpart_machine_id].machine_label_id ?? pair.cross_machine.counterpart_machine_id)
                              : null);
                          const busLabel = pair.bus ? pair.busName ?? busDisplayName(buses[pair.bus], pair.bus) : null;
                          return (
                            <View key={pair.id} style={styles.connRow}>
                              <Text style={styles.connText}>
                                {isOutgoing ? '→' : '←'} {counterpartLabel}
                                {busLabel ? ` · ${busLabel}` : ''} · {pair.status}
                              </Text>
                              {crossName ? (
                                <View style={styles.crossBadge}>
                                  <Text style={styles.crossBadgeText}>crosses to {crossName}</Text>
                                </View>
                              ) : null}
                            </View>
                          );
                        })}
                      </View>
                    ) : node.node_platform !== 'service_bus' ? (
                      <Text style={styles.lastSeenLine}>
                        {online ? 'No live pipeline/pair data for this node.' : 'No connections known without live data.'}
                      </Text>
                    ) : null}

                    {onSelectPlatform && node.node_platform !== 'service_bus' ? (
                      <Text
                        style={[styles.lastSeenLine, { color: colors.accent, fontWeight: '700' }]}
                        onPress={() => onSelectPlatform(String(node.server_id))}
                      >
                        View this service's own detail →
                      </Text>
                    ) : null}
                  </View>
                );
              })
            )}
          </View>
        </>
      )}
    </DiagnosticsModal>
    {visible ? pin.modal : null}
    </>
  );
}
