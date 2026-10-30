import { mergeById } from '../utils/applyDelta';
import type {
  AlertMessage,
  BusEntry,
  BusStatus,
  ConfigIssue,
  DeltaMessage,
  MonitoredObject,
  Pair,
  Pipeline,
  PipelineId,
  PlatformDef,
  QueueThresholds,
  RelaySummary,
  SnapshotMessage,
  TopologyEdge,
  TopologyNode,
} from '../types/relay';

export interface MachineData {
  source: 'mock' | 'live' | null;
  seq: number | null;
  lastSnapshotTs: string | null;
  pipelines: Record<string, Pipeline>;
  objects: Record<string, MonitoredObject>;
  topologyNodes: Record<string, TopologyNode>;
  topologyEdges: Record<string, TopologyEdge>;
  pairs: Record<string, Pair>;
  platforms: Record<string, PlatformDef>;
  buses: Record<string, BusEntry>;
  bus: BusStatus | null;
  configIssues: ConfigIssue[];
  thresholds: QueueThresholds | null;
  activeAlerts: Record<string, AlertMessage>;
  alertFeed: AlertMessage[];
  alertHistory: AlertMessage[];
  summary: RelaySummary | null;
}

export function emptyMachineData(): MachineData {
  return {
    source: null,
    seq: null,
    lastSnapshotTs: null,
    pipelines: {},
    objects: {},
    topologyNodes: {},
    topologyEdges: {},
    pairs: {},
    platforms: {},
    buses: {},
    bus: null,
    configIssues: [],
    thresholds: null,
    activeAlerts: {},
    alertFeed: [],
    alertHistory: [],
    summary: null,
  };
}

function edgeKey(e: { id: string; pipelineId?: PipelineId }): string {
  return e.pipelineId != null ? `${e.id}::${e.pipelineId}` : e.id;
}

function busKey(b: BusEntry): string {
  return b.id ?? b.name;
}

function normalizeMachineBusField(raw: BusStatus | BusEntry[] | undefined): {
  busEntries: BusEntry[];
  legacyBus: BusStatus | null;
} {
  if (!raw) return { busEntries: [], legacyBus: null };
  if (Array.isArray(raw)) return { busEntries: raw, legacyBus: null };
  return { busEntries: [], legacyBus: raw };
}

export function applySnapshotToMachineData(msg: SnapshotMessage): MachineData {
  const pipelines: Record<string, Pipeline> = {};
  for (const p of msg.pipelines) pipelines[String(p.pipelineId)] = p;
  const objects: Record<string, MonitoredObject> = {};
  for (const o of msg.objects) objects[o.id] = o;
  const topologyNodes: Record<string, TopologyNode> = {};
  for (const n of msg.topology.nodes) topologyNodes[n.id] = n;
  const topologyEdges: Record<string, TopologyEdge> = {};
  for (const e of msg.topology.edges) topologyEdges[edgeKey(e)] = e;
  const activeAlerts: Record<string, AlertMessage> = {};
  for (const a of msg.activeAlerts) activeAlerts[a.alertId] = a;
  const pairs: Record<string, Pair> = {};
  for (const p of msg.pairs ?? []) pairs[p.id] = p;

  const serverList = msg.servers ?? msg.platforms;
  const platforms: Record<string, PlatformDef> = {};
  if (serverList) {
    for (const p of serverList) platforms[p.id] = p;
  } else {
    for (const n of msg.topology.nodes) {
      if (n.kind === 'platform') platforms[n.id] = { id: n.id, label: n.label ?? n.id, type: n.platform ?? n.id };
    }
  }

  const { busEntries, legacyBus } = normalizeMachineBusField(msg.bus);
  const buses: Record<string, BusEntry> = {};
  for (const b of [...(msg.buses ?? []), ...busEntries]) buses[busKey(b)] = b;

  return {
    source: msg.source ?? null,
    seq: msg.seq,
    lastSnapshotTs: msg.ts,
    pipelines,
    objects,
    topologyNodes,
    topologyEdges,
    pairs,
    platforms,
    buses,
    bus: legacyBus,
    configIssues: msg.configIssues ?? [],
    thresholds: msg.thresholds ?? null,
    activeAlerts,
    alertFeed: msg.activeAlerts,
    alertHistory: [],
    summary: msg.summary ?? null,
  };
}

export function mergeDeltaIntoMachineData(state: MachineData, msg: DeltaMessage): MachineData {
  const pipelines = mergeById(
    state.pipelines,
    msg.pipelines as (Pipeline & { pipelineId: PipelineId })[] | undefined,
    (p) => String(p.pipelineId)
  );
  const objects = mergeById(state.objects, msg.objects as MonitoredObject[] | undefined, (o) => o.id);
  let topologyNodes = mergeById(state.topologyNodes, msg.topology?.nodes as TopologyNode[] | undefined, (n) => n.id);
  const topologyEdges = mergeById(state.topologyEdges, msg.topology?.edges as TopologyEdge[] | undefined, (e) => edgeKey(e));

  const zeroedPairs: Record<string, Pair> = { ...state.pairs };
  for (const [id, p] of Object.entries(state.pairs)) {
    if (p.packets !== 0) zeroedPairs[id] = { ...p, packets: 0 };
  }
  let pairs = mergeById(zeroedPairs, msg.pairs as Pair[] | undefined, (p) => p.id);

  let platforms = state.platforms;
  let removedPlatformIds = new Set<string>();
  const serverList = msg.servers ?? msg.platforms;
  if (serverList) {
    const nextIds = new Set(serverList.map((p) => p.id));
    for (const id of Object.keys(state.platforms)) {
      if (!nextIds.has(id)) removedPlatformIds.add(id);
    }
    const nextPlatforms: Record<string, PlatformDef> = {};
    for (const p of serverList) nextPlatforms[p.id] = p;
    platforms = nextPlatforms;
  }

  let buses = state.buses;
  let legacyBus = state.bus;
  if (msg.bus !== undefined || msg.buses) {
    const { busEntries, legacyBus: nextLegacy } = normalizeMachineBusField(msg.bus);
    if (msg.buses || busEntries.length > 0) {
      const nextBuses: Record<string, BusEntry> = {};
      for (const b of [...(msg.buses ?? []), ...busEntries]) nextBuses[busKey(b)] = b;
      buses = nextBuses;
    }
    if (nextLegacy) legacyBus = nextLegacy;
  }

  const explicitNodeRemoved = new Set<string>([
    ...(msg.removed?.topologyNodes ?? []),
    ...(msg.topology?.removed ?? (msg.topology as { nodesRemoved?: string[] } | undefined)?.nodesRemoved ?? []),
  ]);
  const explicitPairRemoved = new Set<string>([...(msg.removed?.pairs ?? []), ...(msg.pairsRemoved ?? [])]);
  const explicitPipelineRemoved = new Set<string>((msg.removed?.pipelines ?? []).map(String));
  const explicitObjectRemoved = new Set<string>(msg.removed?.objects ?? []);

  let mergedPipelines: Record<string, Pipeline> = pipelines;
  if (explicitPipelineRemoved.size > 0) {
    const filtered: Record<string, Pipeline> = {};
    for (const [id, p] of Object.entries(pipelines)) {
      if (explicitPipelineRemoved.has(id)) continue;
      filtered[id] = p;
    }
    mergedPipelines = filtered;
  }

  let mergedObjects = objects;
  if (explicitObjectRemoved.size > 0) {
    const filtered: Record<string, MonitoredObject> = {};
    for (const [id, o] of Object.entries(objects)) {
      if (explicitObjectRemoved.has(id)) continue;
      filtered[id] = o;
    }
    mergedObjects = filtered;
  }

  if (removedPlatformIds.size > 0 || explicitNodeRemoved.size > 0) {
    const filtered: Record<string, TopologyNode> = {};
    for (const [id, node] of Object.entries(topologyNodes)) {
      const platformOfNode = node.kind === 'platform' ? node.id : node.platform;
      if (platformOfNode && removedPlatformIds.has(platformOfNode)) continue;
      if (explicitNodeRemoved.has(id)) continue;
      filtered[id] = node;
    }
    topologyNodes = filtered;
  }

  if (removedPlatformIds.size > 0 || explicitPairRemoved.size > 0) {
    const filtered: Record<string, Pair> = {};
    for (const [id, pair] of Object.entries(pairs)) {
      if (removedPlatformIds.has(pair.from) || removedPlatformIds.has(pair.to)) continue;
      if (explicitPairRemoved.has(id)) continue;
      filtered[id] = pair;
    }
    pairs = filtered;
  }

  let activeAlerts = state.activeAlerts;
  let alertFeed = state.alertFeed;
  let alertHistory = state.alertHistory;
  if (msg.activeAlerts) {
    const stillActive = new Set(msg.activeAlerts);
    const dropped: AlertMessage[] = [];
    activeAlerts = {};
    for (const [id, alert] of Object.entries(state.activeAlerts)) {
      if (stillActive.has(id)) activeAlerts[id] = alert;
      else dropped.push(alert);
    }
    if (dropped.length > 0) {
      alertFeed = alertFeed.filter((a) => stillActive.has(a.alertId));
      alertHistory = [
        ...dropped.map((a) => ({ ...a, event: 'resolved' as const, resolvedAt: a.resolvedAt ?? msg.ts })),
        ...alertHistory,
      ];
    }
  }

  return {
    ...state,
    seq: msg.seq,
    lastSnapshotTs: msg.ts,
    pipelines: mergedPipelines,
    objects: mergedObjects,
    topologyNodes,
    topologyEdges,
    pairs,
    platforms,
    buses,
    bus: legacyBus,
    configIssues: msg.configIssues ?? state.configIssues,
    thresholds: msg.thresholds ?? state.thresholds,
    activeAlerts,
    alertFeed,
    alertHistory,
    summary: msg.summary ?? state.summary,
  };
}
