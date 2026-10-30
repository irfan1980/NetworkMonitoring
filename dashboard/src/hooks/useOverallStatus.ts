import { useMemo } from 'react';
import { useConnectionStore } from '../stores/connectionStore';
import { colors } from '../theme/colors';
import { usePlatformList } from '../utils/platformGrouping';
import {
  applyBlockedCountToSummary,
  applyConfigIssuesOverride,
  applyPureOutageFloor,
  deriveOverallStatus,
  statusFromSummary,
  type OverallStatus,
} from '../utils/health';

export function useOverallStatus(): OverallStatus {
  const connectionState = useConnectionStore((s) => s.connectionState);
  const pipelines = useConnectionStore((s) => s.pipelines);
  const objects = useConnectionStore((s) => s.objects);
  const topologyEdges = useConnectionStore((s) => s.topologyEdges);
  const summary = useConnectionStore((s) => s.summary);
  const configIssues = useConnectionStore((s) => s.configIssues);
  const topologyNodes = useConnectionStore((s) => s.topologyNodes);
  const activeAlerts = useConnectionStore((s) => s.activeAlerts);
  const platforms = useConnectionStore((s) => s.platforms);
  const machines = useConnectionStore((s) => s.machines);
  const buses = useConnectionStore((s) => s.buses);
  const platformList = usePlatformList();

  const status = useMemo(() => {
    if (summary && connectionState === 'live') {
      return applyBlockedCountToSummary(statusFromSummary(summary), summary, platforms, topologyNodes, activeAlerts);
    }
    return deriveOverallStatus(
      connectionState,
      Object.values(pipelines),
      Object.values(objects),
      Object.values(topologyEdges)
    );
  }, [connectionState, pipelines, objects, topologyEdges, summary, platforms, topologyNodes, activeAlerts]);

  return useMemo((): OverallStatus => {
    const noSnapshot = !summary && Object.keys(machines).length === 0 && Object.keys(topologyNodes).length === 0;
    if (noSnapshot) {
      return { level: 'offline', title: 'CONNECTING...', reason: 'Waiting for the first snapshot from the relay.', color: colors.unknown };
    }
    const result = applyConfigIssuesOverride(applyPureOutageFloor(status, Object.values(topologyEdges), Object.values(objects)), configIssues);
    if (result.level !== 'good') return result;
    const alertCount = Object.values(activeAlerts).filter((a) => a.event !== 'resolved' && !a.resolvedAt).length;
    const greyNodes = platformList.filter((p) => {
      const node = topologyNodes[p.id];
      return !node || node.dbOk === false || !(node.senderOk && node.receiverOk);
    }).length;
    const busDown = Object.values(buses).some((b) => b.ok === false);
    if (alertCount + greyNodes === 0 && !busDown) return result;
    const parts = [
      alertCount ? `${alertCount} active alert${alertCount === 1 ? '' : 's'}` : null,
      greyNodes ? `${greyNodes} service${greyNodes === 1 ? '' : 's'} not confirmed healthy yet` : null,
      busDown ? 'a Service Bus is down' : null,
    ];
    return { level: 'attention', title: 'ATTENTION', reason: parts.filter(Boolean).join(', ') + '.', color: colors.warn };
  }, [status, configIssues, topologyEdges, objects, summary, machines, topologyNodes, activeAlerts, buses, platformList]);
}
