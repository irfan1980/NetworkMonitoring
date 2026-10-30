import { create } from 'zustand';
import type { LaneTuning } from '../config/laneTuning';
import { mergeById } from '../utils/applyDelta';
import { aggregateMachineSummaries } from '../utils/relaySummary';
import {
  applySnapshotToMachineData,
  emptyMachineData,
  mergeDeltaIntoMachineData,
  type MachineData,
} from './machinePayload';
import type {
  AckBroadcast,
  AckResultMessage,
  AlertMessage,
  BusEntry,
  BusStatus,
  ConfigIssue,
  ConnectionState,
  DeltaMessage,
  MachineInfo,
  MachineSocketState,
  MonitoredObject,
  Pair,
  Pipeline,
  PipelineId,
  PlatformDef,
  QueueThresholds,
  RelaySummary,
  ScheduleSummary,
  SnapshotMessage,
  StandaloneServiceNode,
  TopologyEdge,
  TopologyNode,
} from '../types/relay';

function edgeKey(e: { id: string; pipelineId?: PipelineId }): string {
  return e.pipelineId != null ? `${e.id}::${e.pipelineId}` : e.id;
}

function busKey(b: BusEntry): string {
  return b.id ?? b.name;
}

function mergeEstateView(
  perMachine: Record<number, MachineData>,
  rootAlerts: Record<string, AlertMessage>,
  rootBuses: Record<string, BusEntry>,
  rootConfigIssues: ConfigIssue[] = [],
  rootFlow: { pairs: Record<string, Pair>; pipelines: Record<string, Pipeline> } = { pairs: {}, pipelines: {} }
) {
  let pipelines: Record<string, Pipeline> = { ...rootFlow.pipelines };
  let objects: Record<string, MonitoredObject> = {};
  let topologyNodes: Record<string, TopologyNode> = {};
  let topologyEdges: Record<string, TopologyEdge> = {};
  let pairs: Record<string, Pair> = { ...rootFlow.pairs };
  let platforms: Record<string, PlatformDef> = {};
  let buses: Record<string, BusEntry> = {};
  let bus: BusStatus | null = null;
  const configIssueKey = (ci: ConfigIssue) => `${ci.code}:${JSON.stringify(ci.where ?? {})}`;
  const configIssuesMap: Record<string, ConfigIssue> = {};
  for (const ci of rootConfigIssues) configIssuesMap[configIssueKey(ci)] = ci;
  let thresholds: QueueThresholds | null = null;
  let activeAlerts: Record<string, AlertMessage> = { ...rootAlerts };
  let alertHistory: AlertMessage[] = [];
  const perMachineSummaries: RelaySummary[] = [];

  for (const md of Object.values(perMachine)) {
    pipelines = { ...pipelines, ...md.pipelines };
    objects = { ...objects, ...md.objects };
    topologyNodes = { ...topologyNodes, ...md.topologyNodes };
    topologyEdges = { ...topologyEdges, ...md.topologyEdges };
    pairs = { ...pairs, ...md.pairs };
    platforms = { ...platforms, ...md.platforms };
    buses = { ...buses, ...md.buses };
    if (bus == null && md.bus) bus = md.bus;
    for (const ci of md.configIssues) {
      configIssuesMap[configIssueKey(ci)] = ci;
    }
    if (thresholds == null && md.thresholds) thresholds = md.thresholds;
    activeAlerts = { ...activeAlerts, ...md.activeAlerts };
    alertHistory = [...alertHistory, ...md.alertHistory];
    if (md.summary) perMachineSummaries.push(md.summary);
  }

  const alertFeed = Object.values(activeAlerts).sort((a, b) => (b.ts ?? '').localeCompare(a.ts ?? ''));
  const seenHistory = new Set<string>();
  const dedupedHistory = alertHistory.filter((a) => {
    if (seenHistory.has(a.alertId)) return false;
    seenHistory.add(a.alertId);
    return true;
  });

  const mergedBuses: Record<string, BusEntry> = { ...rootBuses, ...buses };

  return {
    pipelines,
    objects,
    topologyNodes,
    topologyEdges,
    pairs,
    platforms,
    buses: mergedBuses,
    bus,
    configIssues: Object.values(configIssuesMap),
    thresholds,
    activeAlerts,
    alertFeed,
    alertHistory: dedupedHistory,
    summary: aggregateMachineSummaries(perMachineSummaries),
  };
}

const flowOf = (st: { rootPairs: Record<string, Pair>; rootPipelines: Record<string, Pipeline> }) => ({ pairs: st.rootPairs, pipelines: st.rootPipelines });

interface ConnectionStoreState {
  connectionState: ConnectionState;
  connectionDownSinceMs: number | null;
  reconnectAttempts: number;
  nextRetryAtMs: number | null;
  everConnected: boolean;
  unauthorizedReason: string | null;
  retryToken: number;
  source: 'mock' | 'live' | null;
  seq: number | null;
  lastSnapshotTs: string | null;

  machines: Record<number, MachineInfo>;
  machineSocketState: Record<number, MachineSocketState>;
  perMachine: Record<number, MachineData>;
  rootAlerts: Record<string, AlertMessage>;
  rootBuses: Record<string, BusEntry>;
  rootConfigIssues: ConfigIssue[];
  rootPairs: Record<string, Pair>;
  rootPipelines: Record<string, Pipeline>;
  estateServices: StandaloneServiceNode[];
  laneTuning: Partial<LaneTuning> | null;
  schedules: Record<string, ScheduleSummary>;
  dataTick: number;

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
  silencedAlertIds: Record<string, number>;
  summary: RelaySummary | null;
  ackErrors: Record<string, AckResultMessage['error']>;
  wsActions: {
    sendSilence: (alertId: string) => void;
    sendAck: (alertId: string, pin: string) => void;
  } | null;

  setConnectionState: (s: ConnectionState) => void;
  setUnauthorized: (reason: string | null) => void;
  manualRetry: () => void;
  noteReconnectScheduled: (delayMs: number) => void;
  setWsActions: (actions: ConnectionStoreState['wsActions']) => void;
  setSchedules: (list: ScheduleSummary[]) => void;
  setMachines: (list: MachineInfo[]) => void;

  applySnapshot: (msg: SnapshotMessage) => void;
  applyDelta: (msg: DeltaMessage) => void;

  applyMachineInventorySnapshot: (
    machines: MachineInfo[],
    alerts: AlertMessage[],
    services?: StandaloneServiceNode[],
    rootBuses?: BusEntry[],
    rootConfigIssues?: ConfigIssue[]
  ) => void;
  applyMachineInventoryDelta: (
    changed: Array<Partial<MachineInfo> & { machine_id: number }>,
    removed: number[],
    alertsFull: AlertMessage[] | null,
    alertIdsStill: string[] | null,
    services?: StandaloneServiceNode[],
    rootBuses?: BusEntry[]
  ) => void;
  setRootServiceFlow: (pairs?: Pair[], pipelines?: Pipeline[]) => void;
  setMachineSocketState: (machineId: number, state: MachineSocketState) => void;

  applyMachineSnapshot: (machineId: number, msg: SnapshotMessage) => void;
  applyMachineDelta: (machineId: number, msg: DeltaMessage) => void;
  applyMachineAlert: (machineId: number, msg: AlertMessage) => void;
  applyMachineAck: (machineId: number, msg: AckBroadcast) => void;
  applyMachineAckResult: (machineId: number, msg: AckResultMessage) => void;

  applyAlert: (msg: AlertMessage) => void;
  applyAck: (msg: AckBroadcast) => void;
  applyAckResult: (msg: AckResultMessage) => void;
  clearAckError: (alertId: string) => void;
  silenceLocally: (alertId: string, minutes: number) => void;
  reset: () => void;
}

const initialData = {
  source: null as 'mock' | 'live' | null,
  seq: null as number | null,
  lastSnapshotTs: null as string | null,
  pipelines: {} as Record<string, Pipeline>,
  objects: {} as Record<string, MonitoredObject>,
  topologyNodes: {} as Record<string, TopologyNode>,
  topologyEdges: {} as Record<string, TopologyEdge>,
  pairs: {} as Record<string, Pair>,
  platforms: {} as Record<string, PlatformDef>,
  buses: {} as Record<string, BusEntry>,
  bus: null as BusStatus | null,
  configIssues: [] as ConfigIssue[],
  thresholds: null as QueueThresholds | null,
  activeAlerts: {} as Record<string, AlertMessage>,
  alertFeed: [] as AlertMessage[],
  alertHistory: [] as AlertMessage[],
  summary: null as RelaySummary | null,
  machines: {} as Record<number, MachineInfo>,
  machineSocketState: {} as Record<number, MachineSocketState>,
  perMachine: {} as Record<number, MachineData>,
  rootAlerts: {} as Record<string, AlertMessage>,
  rootBuses: {} as Record<string, BusEntry>,
  rootConfigIssues: [] as ConfigIssue[],
  rootPairs: {} as Record<string, Pair>,
  rootPipelines: {} as Record<string, Pipeline>,
  estateServices: [] as StandaloneServiceNode[],
  laneTuning: null as Partial<LaneTuning> | null,
  schedules: {} as Record<string, ScheduleSummary>,
  dataTick: 0,
};

export const useConnectionStore = create<ConnectionStoreState>((set, get) => ({
  connectionState: 'connecting',
  connectionDownSinceMs: Date.now(),
  reconnectAttempts: 0,
  nextRetryAtMs: null,
  everConnected: false,
  unauthorizedReason: null,
  retryToken: 0,
  ...initialData,
  silencedAlertIds: {},
  ackErrors: {},
  wsActions: null,

  setConnectionState: (s) =>
    set((prev) =>
      s === 'live'
        ? {
            connectionState: s,
            connectionDownSinceMs: null,
            reconnectAttempts: 0,
            nextRetryAtMs: null,
            everConnected: true,
          }
        : {
            connectionState: s,
            connectionDownSinceMs: prev.connectionDownSinceMs ?? Date.now(),
          }
    ),
  setUnauthorized: (reason) =>
    set({
      connectionState: 'unauthorized',
      unauthorizedReason: reason,
      nextRetryAtMs: null,
    }),
  manualRetry: () =>
    set((prev) => ({
      connectionState: 'connecting',
      unauthorizedReason: null,
      reconnectAttempts: 0,
      nextRetryAtMs: null,
      connectionDownSinceMs: Date.now(),
      retryToken: prev.retryToken + 1,
    })),
  noteReconnectScheduled: (delayMs) =>
    set((prev) => ({
      reconnectAttempts: prev.reconnectAttempts + 1,
      nextRetryAtMs: Date.now() + delayMs,
    })),
  setWsActions: (actions) => set({ wsActions: actions }),
  setSchedules: (list) => {
    const schedules: Record<string, ScheduleSummary> = {};
    for (const s of list) schedules[String(s.id)] = s;
    set({ schedules });
  },
  setMachines: (list) => {
    const machines: Record<number, MachineInfo> = {};
    for (const m of list) machines[m.machine_id] = m;
    set({ machines });
  },

  applySnapshot: (msg) => {
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
    const buses: Record<string, BusEntry> = {};
    for (const b of msg.buses ?? []) buses[busKey(b)] = b;

    set({
      source: msg.source ?? get().source,
      seq: msg.seq,
      lastSnapshotTs: msg.ts,
      pipelines,
      objects,
      topologyNodes,
      topologyEdges,
      pairs,
      platforms,
      buses,
      bus: (msg.bus as BusStatus | undefined) ?? get().bus,
      configIssues: msg.configIssues ?? get().configIssues,
      thresholds: msg.thresholds ?? get().thresholds,
      activeAlerts,
      alertFeed: msg.activeAlerts,
      summary: msg.summary ?? get().summary,
    });
  },

  applyDelta: (msg) => {
    const state = get();
    const pipelines = mergeById(
      state.pipelines,
      msg.pipelines as (Pipeline & { pipelineId: PipelineId })[] | undefined,
      (p) => String(p.pipelineId)
    );
    const objects = mergeById(state.objects, msg.objects as MonitoredObject[] | undefined, (o) => o.id);
    let topologyNodes = mergeById(
      state.topologyNodes,
      msg.topology?.nodes as TopologyNode[] | undefined,
      (n) => n.id
    );
    const topologyEdges = mergeById(
      state.topologyEdges,
      msg.topology?.edges as TopologyEdge[] | undefined,
      (e) => edgeKey(e)
    );
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
    if (msg.buses) {
      const nextBuses: Record<string, BusEntry> = {};
      for (const b of msg.buses) nextBuses[busKey(b)] = b;
      buses = nextBuses;
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

    set({
      seq: msg.seq,
      lastSnapshotTs: msg.ts,
      pipelines: mergedPipelines,
      objects: mergedObjects,
      topologyNodes,
      topologyEdges,
      pairs,
      platforms,
      buses,
      bus: (msg.bus as BusStatus | undefined) ?? state.bus,
      configIssues: msg.configIssues ?? state.configIssues,
      thresholds: msg.thresholds ?? state.thresholds,
      activeAlerts,
      alertFeed,
      alertHistory,
      summary: msg.summary ?? state.summary,
    });
  },

  applyMachineInventorySnapshot: (machinesList, alerts, services, rootBusesList, rootConfigIssuesList) => {
    const machines: Record<number, MachineInfo> = {};
    for (const m of machinesList) machines[m.machine_id] = m;
    const rootAlerts: Record<string, AlertMessage> = {};
    for (const a of alerts) rootAlerts[a.alertId] = a;
    const rootBuses: Record<string, BusEntry> = {};
    for (const b of rootBusesList ?? []) rootBuses[busKey(b)] = b;
    const estateServices = services ?? get().estateServices;
    const rootConfigIssues = rootConfigIssuesList ?? get().rootConfigIssues;
    set({ machines, rootAlerts, rootBuses, rootConfigIssues, estateServices });
    const merged = mergeEstateView(get().perMachine, rootAlerts, rootBuses, rootConfigIssues, flowOf(get()));
    set({ ...merged, dataTick: get().dataTick + 1 });
  },

  applyMachineInventoryDelta: (changed, removed, alertsFull, alertIdsStill, services, rootBusesList) => {
    const state = get();
    const machines = { ...state.machines };
    for (const c of changed) {
      machines[c.machine_id] = { ...(machines[c.machine_id] as MachineInfo), ...c } as MachineInfo;
    }
    for (const id of removed) delete machines[id];

    let rootAlerts = state.rootAlerts;
    if (alertsFull) {
      rootAlerts = {};
      for (const a of alertsFull) rootAlerts[a.alertId] = a;
    } else if (alertIdsStill) {
      const still = new Set(alertIdsStill);
      const next: Record<string, AlertMessage> = {};
      for (const [id, a] of Object.entries(state.rootAlerts)) if (still.has(id)) next[id] = a;
      rootAlerts = next;
    }

    let rootBuses = state.rootBuses;
    if (rootBusesList) {
      rootBuses = {};
      for (const b of rootBusesList) rootBuses[busKey(b)] = b;
    }
    const estateServices = services ?? state.estateServices;

    set({ machines, rootAlerts, rootBuses, estateServices });
    const merged = mergeEstateView(get().perMachine, rootAlerts, rootBuses, state.rootConfigIssues, flowOf(state));
    set({ ...merged, dataTick: get().dataTick + 1 });
  },

  setRootServiceFlow: (pairsList, pipelinesList) => {
    const state = get();
    const rootPairs = pairsList ? Object.fromEntries(pairsList.map((p) => [p.id, p])) : state.rootPairs;
    const rootPipelines = pipelinesList ? Object.fromEntries(pipelinesList.map((p) => [String(p.pipelineId), p])) : state.rootPipelines;
    if (rootPairs === state.rootPairs && rootPipelines === state.rootPipelines) return;
    set({ rootPairs, rootPipelines });
    const merged = mergeEstateView(state.perMachine, state.rootAlerts, state.rootBuses, state.rootConfigIssues, { pairs: rootPairs, pipelines: rootPipelines });
    set({ ...merged, dataTick: get().dataTick + 1 });
  },

  setMachineSocketState: (machineId, s) =>
    set((prev) => ({ machineSocketState: { ...prev.machineSocketState, [machineId]: s } })),

  applyMachineSnapshot: (machineId, msg) => {
    const data = applySnapshotToMachineData(msg);
    const perMachine = { ...get().perMachine, [machineId]: data };
    const merged = mergeEstateView(perMachine, get().rootAlerts, get().rootBuses, get().rootConfigIssues, flowOf(get()));
    set({ perMachine, ...merged, dataTick: get().dataTick + 1 });
  },

  applyMachineDelta: (machineId, msg) => {
    const state = get();
    const existing = state.perMachine[machineId] ?? emptyMachineData();
    const data = mergeDeltaIntoMachineData(existing, msg);
    const perMachine = { ...state.perMachine, [machineId]: data };
    const merged = mergeEstateView(perMachine, state.rootAlerts, state.rootBuses, state.rootConfigIssues, flowOf(state));
    set({ perMachine, ...merged, dataTick: state.dataTick + 1 });
  },

  applyMachineAlert: (machineId, msg) => {
    const state = get();
    const existing = state.perMachine[machineId] ?? emptyMachineData();
    let data: MachineData;
    if (msg.event === 'resolved') {
      const { [msg.alertId]: _removed, ...restActive } = existing.activeAlerts;
      data = {
        ...existing,
        activeAlerts: restActive,
        alertFeed: existing.alertFeed.filter((a) => a.alertId !== msg.alertId),
        alertHistory: [msg, ...existing.alertHistory.filter((a) => a.alertId !== msg.alertId)],
      };
    } else {
      data = {
        ...existing,
        activeAlerts: { ...existing.activeAlerts, [msg.alertId]: msg },
        alertFeed: [msg, ...existing.alertFeed.filter((a) => a.alertId !== msg.alertId)],
      };
    }
    const perMachine = { ...state.perMachine, [machineId]: data };
    const merged = mergeEstateView(perMachine, state.rootAlerts, state.rootBuses, state.rootConfigIssues, flowOf(state));
    set({ perMachine, ...merged });
  },

  applyMachineAck: (machineId, msg) => {
    const state = get();
    const existing = state.perMachine[machineId] ?? emptyMachineData();
    const current = existing.activeAlerts[msg.alertId];
    const { [msg.alertId]: _cleared, ...restAckErrors } = state.ackErrors;
    if (!current) {
      set({ ackErrors: restAckErrors });
      return;
    }
    const updated: AlertMessage = { ...current, acknowledged: true, acknowledged_by: msg.ackedBy, acknowledged_at: msg.ackedAt };
    const data: MachineData = {
      ...existing,
      activeAlerts: { ...existing.activeAlerts, [msg.alertId]: updated },
      alertFeed: existing.alertFeed.map((a) => (a.alertId === msg.alertId ? updated : a)),
    };
    const perMachine = { ...state.perMachine, [machineId]: data };
    const merged = mergeEstateView(perMachine, state.rootAlerts, state.rootBuses, state.rootConfigIssues, flowOf(state));
    set({ perMachine, ...merged, ackErrors: restAckErrors });
  },

  applyMachineAckResult: (_machineId, msg) => {
    if (msg.ok) return;
    set({ ackErrors: { ...get().ackErrors, [msg.alertId]: msg.error ?? msg.reason ?? 'unknown' } });
  },

  applyAlert: (msg) => {
    const state = get();

    if (msg.event === 'resolved') {
      const { [msg.alertId]: _removed, ...restActive } = state.activeAlerts;
      set({
        activeAlerts: restActive,
        alertFeed: state.alertFeed.filter((a) => a.alertId !== msg.alertId),
        alertHistory: [msg, ...state.alertHistory.filter((a) => a.alertId !== msg.alertId)],
      });
      return;
    }

    set({
      activeAlerts: { ...state.activeAlerts, [msg.alertId]: msg },
      alertFeed: [msg, ...state.alertFeed.filter((a) => a.alertId !== msg.alertId)],
    });
  },

  applyAck: (msg) => {
    const state = get();
    const { [msg.alertId]: _cleared, ...restAckErrors } = state.ackErrors;
    const existing = state.activeAlerts[msg.alertId];
    if (!existing) {
      set({ ackErrors: restAckErrors });
      return;
    }
    const updated: AlertMessage = {
      ...existing,
      acknowledged: true,
      acknowledged_by: msg.ackedBy,
      acknowledged_at: msg.ackedAt,
    };
    set({
      activeAlerts: { ...state.activeAlerts, [msg.alertId]: updated },
      alertFeed: state.alertFeed.map((a) => (a.alertId === msg.alertId ? updated : a)),
      ackErrors: restAckErrors,
    });
  },

  applyAckResult: (msg) => {
    if (msg.ok) return;
    set({ ackErrors: { ...get().ackErrors, [msg.alertId]: msg.error ?? msg.reason ?? 'unknown' } });
  },

  clearAckError: (alertId) => {
    const { [alertId]: _cleared, ...rest } = get().ackErrors;
    set({ ackErrors: rest });
  },

  silenceLocally: (alertId, minutes) => {
    set({
      silencedAlertIds: { ...get().silencedAlertIds, [alertId]: Date.now() + minutes * 60_000 },
    });
  },

  reset: () =>
    set({
      ...initialData,
      connectionState: 'connecting',
      connectionDownSinceMs: Date.now(),
      reconnectAttempts: 0,
      nextRetryAtMs: null,
      everConnected: false,
      unauthorizedReason: null,
    }),
}));
