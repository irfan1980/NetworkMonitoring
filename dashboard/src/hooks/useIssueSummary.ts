import { useMemo } from 'react';
import { useConnectionStore } from '../stores/connectionStore';
import { usePlatformList } from '../utils/platformGrouping';

export function useHasIssues(): boolean {
  const platformList = usePlatformList();
  const topologyNodes = useConnectionStore((s) => s.topologyNodes);
  const buses = useConnectionStore((s) => s.buses);
  const bus = useConnectionStore((s) => s.bus);
  const pairs = useConnectionStore((s) => s.pairs);
  const configIssues = useConnectionStore((s) => s.configIssues);
  const activeAlerts = useConnectionStore((s) => s.activeAlerts);

  return useMemo(() => {
    for (const p of platformList) {
      const node = topologyNodes[p.id];
      const grey = !node || node.dbOk === false || !(node.senderOk && node.receiverOk);
      if (grey) return true;
    }

    const busList = Object.values(buses);
    if (busList.length > 0) {
      if (busList.some((b) => b.ok === false)) return true;
    } else if (bus?.ok === false) {
      return true;
    }

    if (Object.values(pairs).some((p) => !!p.warning)) return true;

    if (Object.values(pairs).some((p) => p.missingEndReason === 'disabled')) return true;

    if (configIssues.length > 0) return true;

    if (Object.values(activeAlerts).some((a) => a.event !== 'resolved' && !a.resolvedAt)) return true;

    return false;
  }, [platformList, topologyNodes, buses, bus, pairs, configIssues, activeAlerts]);
}
