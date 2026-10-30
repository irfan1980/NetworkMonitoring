import { useMemo } from 'react';
import { useConnectionStore } from '../stores/connectionStore';
import type { MachineNode, PlatformDef } from '../types/relay';

export function usePlatformList(): PlatformDef[] {
  const platforms = useConnectionStore((s) => s.platforms);
  const machines = useConnectionStore((s) => s.machines);
  const topologyNodes = useConnectionStore((s) => s.topologyNodes);
  const pairs = useConnectionStore((s) => s.pairs);
  const estateServices = useConnectionStore((s) => s.estateServices);
  return useMemo(() => {
    const fromMachines: Record<string, PlatformDef> = {};
    for (const machine of Object.values(machines)) {
      for (const p of machine.platforms ?? []) fromMachines[p.id] = p;
    }
    const merged: Record<string, PlatformDef> = { ...fromMachines, ...platforms };

    const defs =
      Object.values(merged).length > 0
        ? Object.values(merged)
        : Object.values(topologyNodes)
            .filter((n) => n.kind === 'platform')
            .map((n) => ({ id: n.id, label: n.label ?? n.id, type: n.platform ?? n.id }));

    const known = new Set([...defs.map((d) => d.id), ...estateServices.map((sv) => String(sv.server_id))]);
    const placeholders: PlatformDef[] = [];
    for (const pair of Object.values(pairs)) {
      for (const id of [pair.from, pair.to]) {
        if (!known.has(id)) {
          known.add(id);
          placeholders.push({ id, label: `Unknown server ${id}`, type: '' });
        }
      }
    }
    return placeholders.length > 0 ? [...defs, ...placeholders] : defs;
  }, [platforms, machines, topologyNodes, pairs, estateServices]);
}

export function usePlatformMachineMap(): Record<string, number> {
  const perMachine = useConnectionStore((s) => s.perMachine);
  const machines = useConnectionStore((s) => s.machines);
  return useMemo(() => {
    const map: Record<string, number> = {};
    for (const machine of Object.values(machines)) {
      for (const p of machine.platforms ?? []) map[p.id] = machine.machine_id;
    }
    for (const [machineIdStr, data] of Object.entries(perMachine)) {
      const machineId = Number(machineIdStr);
      for (const platformId of Object.keys(data.platforms)) map[platformId] = machineId;
    }
    return map;
  }, [perMachine, machines]);
}

export function useMachineNodesByPlatform(): Record<string, MachineNode> {
  const machines = useConnectionStore((s) => s.machines);
  return useMemo(() => {
    const map: Record<string, MachineNode> = {};
    for (const machine of Object.values(machines)) {
      for (const node of machine.nodes ?? []) map[String(node.server_id)] = node;
    }
    return map;
  }, [machines]);
}

export function usePipelinePlatformMap(): Record<string, string> {
  const topologyNodes = useConnectionStore((s) => s.topologyNodes);
  return useMemo(() => {
    const map: Record<string, string> = {};
    for (const node of Object.values(topologyNodes)) {
      if (node.kind !== 'platform' || !node.pipelines) continue;
      for (const pipelineId of node.pipelines) map[String(pipelineId)] = node.id;
    }
    return map;
  }, [topologyNodes]);
}
