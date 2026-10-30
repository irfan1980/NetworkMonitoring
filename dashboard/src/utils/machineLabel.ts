import { useMemo } from 'react';
import { useConnectionStore } from '../stores/connectionStore';
import { usePlatformList, usePlatformMachineMap } from './platformGrouping';

export function formatMachineLabel(name: string, id: number | string, maxChars?: number): string {
  const suffix = ` (${id})`;
  if (maxChars == null || name.length + suffix.length <= maxChars) return `${name}${suffix}`;
  const room = Math.max(1, maxChars - suffix.length - 1);
  return `${name.slice(0, room)}…${suffix}`;
}

export interface MachineLabeler {
  machine: (machineId: number | null | undefined, maxChars?: number) => string | null;
  machineOfPlatform: (platformId: string) => string | null;
  platform: (platformId: string, fallback: string) => string;
  platformTitle: (platformId: string) => string;
  pair: (fromId: string, toId: string, fallback: string) => string;
}

export function useMachineLabeler(): MachineLabeler {
  const machines = useConnectionStore((s) => s.machines);
  const platformList = usePlatformList();
  const platformMachineMap = usePlatformMachineMap();
  return useMemo(() => {
    const labelById: Record<string, string> = {};
    for (const p of platformList) labelById[p.id] = p.label;

    const machine = (machineId: number | null | undefined, maxChars?: number) => {
      if (machineId == null) return null;
      const info = machines[machineId];
      return info ? formatMachineLabel(info.machine_name, info.machine_label_id ?? machineId, maxChars) : null;
    };
    const machineOfPlatform = (platformId: string) => machine(platformMachineMap[platformId]);

    const sameName: Record<string, number> = {};
    for (const p of platformList) {
      const mid = platformMachineMap[p.id];
      if (mid == null) continue;
      const k = `${mid}|${p.label}`;
      sameName[k] = (sameName[k] ?? 0) + 1;
    }
    const platformPart = (platformId: string) => {
      const label = labelById[platformId] ?? platformId;
      const mid = platformMachineMap[platformId];
      return mid != null && (sameName[`${mid}|${label}`] ?? 0) > 1 ? `${label} ${platformId}` : label;
    };

    const platformTitle = (platformId: string) => {
      const label = `${labelById[platformId] ?? platformId} (${platformId})`;
      const mid = platformMachineMap[platformId];
      const name = mid != null ? machines[mid]?.machine_name : undefined;
      return name ? `${name} · ${label}` : label;
    };

    return {
      machine,
      platformTitle,
      machineOfPlatform,
      platform: (platformId, fallback) => {
        const m = machineOfPlatform(platformId);
        return m ? `${m} | ${platformPart(platformId)}` : fallback;
      },
      pair: (fromId, toId, fallback) => {
        const a = machineOfPlatform(fromId);
        const b = machineOfPlatform(toId);
        return a && b ? `${a} -> ${b} | ${platformPart(fromId)} -> ${platformPart(toId)}` : fallback;
      },
    };
  }, [machines, platformList, platformMachineMap]);
}
