import { useEffect, useRef } from 'react';
import { FIXTURE_VARIANT, FORCE_FIXTURE, RELAY_WS_URL, machineWsUrl } from '../config/env';
import { withSessionToken } from '../api/relayHttp';
import { useAuthStore } from '../stores/authStore';
import fixtureSnapshot4 from '../fixtures/snapshot.json';
import fixtureSnapshot3 from '../fixtures/snapshot.3.json';
import { FIXTURE_SERVICES, FIXTURE_SERVICE_ALERT, FIXTURE_SERVICE_PAIRS, FIXTURE_SERVICE_PIPELINES } from '../fixtures/services';
import { FIXTURE_SCHEDULER_MACHINES, FIXTURE_SCHEDULES } from '../fixtures/schedules';
import { useConnectionStore } from '../stores/connectionStore';
import { useDeviceStore } from '../stores/deviceStore';
import { useUIStore } from '../stores/uiStore';
import type {
  AckBroadcast,
  AckResultMessage,
  AlertMessage,
  BusEntry,
  DeltaMessage,
  ConfigIssue,
  MachineInfo,
  RelayInMessage,
  ScheduleSummary,
  SnapshotMessage,
  StandaloneServiceNode,
  Pair,
  Pipeline,
} from '../types/relay';

const RECONNECT_MIN_MS = 1000;
const RECONNECT_MAX_MS = 15000;

interface ChildSocket {
  ws: WebSocket | null;
  seqRef: number | null;
  backoffMs: number;
  reconnectTimer: ReturnType<typeof setTimeout> | null;
  closedByUs: boolean;
}

export function useRelaySocket() {
  const { clientId, device, ready, init } = useDeviceStore();
  const applySnapshot = useConnectionStore((s) => s.applySnapshot);
  const applyDelta = useConnectionStore((s) => s.applyDelta);
  const setConnectionState = useConnectionStore((s) => s.setConnectionState);
  const noteReconnectScheduled = useConnectionStore((s) => s.noteReconnectScheduled);
  const setWsActions = useConnectionStore((s) => s.setWsActions);
  const retryToken = useConnectionStore((s) => s.retryToken);
  const showError = useUIStore((s) => s.showError);

  const rootWsRef = useRef<WebSocket | null>(null);
  const rootBackoffRef = useRef(RECONNECT_MIN_MS);
  const rootReconnectTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const rootSeqRef = useRef<number | null>(null);
  const closedByUsRef = useRef(false);

  const childSocketsRef = useRef<Record<number, ChildSocket>>({});
  const allChildWsRef = useRef<WebSocket[]>([]);

  useEffect(() => {
    init();
  }, [init]);

  useEffect(() => {
    if (!ready || !clientId) return;

    if (FORCE_FIXTURE) {
      const fixtureSnapshot = FIXTURE_VARIANT === '3' ? fixtureSnapshot3 : fixtureSnapshot4;
      applySnapshot(fixtureSnapshot as unknown as SnapshotMessage);
      useConnectionStore.getState().setMachines(FIXTURE_SCHEDULER_MACHINES);
      useConnectionStore.getState().setSchedules(FIXTURE_SCHEDULES);
      if (FIXTURE_VARIANT === '4') {
        useConnectionStore.setState((st) => ({
          estateServices: FIXTURE_SERVICES,
          activeAlerts: { ...st.activeAlerts, [FIXTURE_SERVICE_ALERT.alertId]: FIXTURE_SERVICE_ALERT },
        }));
        useConnectionStore.setState((st) => ({
          rootPairs: Object.fromEntries(FIXTURE_SERVICE_PAIRS.map((p) => [p.id, p])),
          rootPipelines: Object.fromEntries(FIXTURE_SERVICE_PIPELINES.map((p) => [String(p.pipelineId), p])),
          pairs: { ...st.pairs, ...Object.fromEntries(FIXTURE_SERVICE_PAIRS.map((p) => [p.id, p])) },
          pipelines: { ...st.pipelines, ...Object.fromEntries(FIXTURE_SERVICE_PIPELINES.map((p) => [String(p.pipelineId), p])) },
        }));
      }
      setConnectionState('live');
      return;
    }

    closedByUsRef.current = false;

    function closeChildSocket(machineId: number) {
      const child = childSocketsRef.current[machineId];
      if (!child) return;
      child.closedByUs = true;
      if (child.reconnectTimer) clearTimeout(child.reconnectTimer);
      child.ws?.close();
      delete childSocketsRef.current[machineId];
      allChildWsRef.current = allChildWsRef.current.filter((w) => w !== child.ws);
    }

    function openChildSocket(machineId: number) {
      if (childSocketsRef.current[machineId]) return;
      const child: ChildSocket = { ws: null, seqRef: null, backoffMs: RECONNECT_MIN_MS, reconnectTimer: null, closedByUs: false };
      childSocketsRef.current[machineId] = child;

      function scheduleReconnect() {
        if (child.closedByUs) return;
        useConnectionStore.getState().setMachineSocketState(machineId, 'reconnecting');
        if (child.reconnectTimer) clearTimeout(child.reconnectTimer);
        const delay = child.backoffMs;
        child.reconnectTimer = setTimeout(connectChild, delay);
        child.backoffMs = Math.min(child.backoffMs * 2, RECONNECT_MAX_MS);
      }

      function handleChildMessage(raw: string) {
        let msg: RelayInMessage;
        try {
          msg = JSON.parse(raw);
        } catch {
          return;
        }
        const store = useConnectionStore.getState();

        if (msg.type === 'snapshot') {
          const snapshot = msg as SnapshotMessage;
          child.seqRef = snapshot.seq;
          child.backoffMs = RECONNECT_MIN_MS;
          store.applyMachineSnapshot(machineId, snapshot);
          store.setMachineSocketState(machineId, 'live');
          return;
        }
        if (msg.type === 'delta') {
          const delta = msg as DeltaMessage;
          const prevSeq = child.seqRef;
          if (prevSeq != null && delta.seq !== prevSeq + 1) {
            child.ws?.send(JSON.stringify({ type: 'resync' }));
            return;
          }
          child.seqRef = delta.seq;
          store.applyMachineDelta(machineId, delta);
          store.setMachineSocketState(machineId, 'live');
          return;
        }
        if (msg.type === 'alert') {
          store.applyMachineAlert(machineId, msg as AlertMessage);
          return;
        }
        if (msg.type === 'ack') {
          store.applyMachineAck(machineId, msg as AckBroadcast);
          return;
        }
        if (msg.type === 'ackResult') {
          store.applyMachineAckResult(machineId, msg as AckResultMessage);
        }
      }

      function connectChild() {
        if (child.closedByUs) return;
        useConnectionStore.getState().setMachineSocketState(machineId, child.ws ? 'reconnecting' : 'connecting');
        try {
          const ws = new WebSocket(withSessionToken(machineWsUrl(machineId)));
          child.ws = ws;
          allChildWsRef.current.push(ws);

          ws.onopen = () => {
            child.backoffMs = RECONNECT_MIN_MS;
            ws.send(JSON.stringify({ type: 'hello', device, clientId }));
          };

          ws.onmessage = (event) => handleChildMessage(String(event.data));

          ws.onclose = (event) => {
            allChildWsRef.current = allChildWsRef.current.filter((w) => w !== ws);
            if (child.closedByUs) return;

            if (event.code === 4401) {
              // Unmounting on a confirmed revoke closes this socket; otherwise keep retrying.
              void useAuthStore.getState().expireIfRevoked();
              scheduleReconnect();
              return;
            }
            if (event.code === 4410) {
              // 4410 is an expected race (the root inventory lags this machine's liveness): back off quietly.
              useConnectionStore.getState().setMachineSocketState(machineId, 'closed_offline');
              scheduleReconnect();
              return;
            }
            useConnectionStore.getState().setMachineSocketState(machineId, 'reconnecting');
            scheduleReconnect();
          };

          ws.onerror = () => {
          };
        } catch {
          scheduleReconnect();
        }
      }

      connectChild();
    }

    function reconcileChildSockets(machines: Record<number, MachineInfo>) {
      const onlineIds = new Set(Object.values(machines).filter((m) => m.status === 'online').map((m) => m.machine_id));
      for (const idStr of Object.keys(childSocketsRef.current)) {
        const id = Number(idStr);
        if (!onlineIds.has(id)) closeChildSocket(id);
      }
      for (const id of onlineIds) {
        if (!childSocketsRef.current[id]) openChildSocket(id);
      }
    }

    function scheduleRootReconnect() {
      if (closedByUsRef.current) return;
      setConnectionState('reconnecting');
      if (rootReconnectTimer.current) clearTimeout(rootReconnectTimer.current);
      const delay = rootBackoffRef.current;
      rootReconnectTimer.current = setTimeout(connectRoot, delay);
      noteReconnectScheduled(delay);
      rootBackoffRef.current = Math.min(rootBackoffRef.current * 2, RECONNECT_MAX_MS);
    }

    function handleRootMessage(raw: string) {
      let msg: { type: string; seq: number; ts: string; [k: string]: unknown };
      try {
        msg = JSON.parse(raw);
      } catch {
        return;
      }
      const store = useConnectionStore.getState();

      if (msg.type === 'snapshot') {
        rootSeqRef.current = msg.seq;
        const machines = (msg.machines as MachineInfo[] | undefined) ?? [];
        const alerts = ((msg.activeAlerts as AlertMessage[] | undefined) ?? []).filter(
          (a): a is AlertMessage => typeof a === 'object'
        );
        const services = msg.services as StandaloneServiceNode[] | undefined;
        const rootBuses = msg.buses as BusEntry[] | undefined;
        useConnectionStore.setState({ source: (msg.source as 'mock' | 'live' | undefined) ?? store.source, seq: msg.seq, lastSnapshotTs: msg.ts });
        store.applyMachineInventorySnapshot(machines, alerts, services, rootBuses, msg.configIssues as ConfigIssue[] | undefined);
        store.setRootServiceFlow(msg.pairs as Pair[] | undefined, msg.pipelines as Pipeline[] | undefined);
        const layout = msg.layout as { lane_node_min?: number; lane_bus_min?: number } | undefined;
        if (layout) useConnectionStore.setState({ laneTuning: { nodeMin: layout.lane_node_min, busMin: layout.lane_bus_min } });
        store.setSchedules((msg.schedules as ScheduleSummary[] | undefined) ?? []);
        setConnectionState('live');
        reconcileChildSockets(useConnectionStore.getState().machines);
        return;
      }

      if (msg.type === 'delta') {
        const prevSeq = rootSeqRef.current;
        if (prevSeq != null && msg.seq !== prevSeq + 1) {
          rootWsRef.current?.send(JSON.stringify({ type: 'resync' }));
          return;
        }
        rootSeqRef.current = msg.seq;
        const changedRaw = (msg.machines as { changed?: Array<Partial<MachineInfo> & { machine_id: number }>; removed?: number[] } | undefined);
        const changed = changedRaw?.changed ?? [];
        const removed = changedRaw?.removed ?? [];
        const rawAlerts = msg.activeAlerts as Array<string | AlertMessage> | undefined;
        let alertsFull: AlertMessage[] | null = null;
        let alertIds: string[] | null = null;
        if (rawAlerts) {
          if (rawAlerts.length === 0 || typeof rawAlerts[0] === 'string') alertIds = rawAlerts as string[];
          else alertsFull = rawAlerts as AlertMessage[];
        }
        const services = msg.services as StandaloneServiceNode[] | undefined;
        const rootBuses = msg.buses as BusEntry[] | undefined;
        useConnectionStore.setState({ seq: msg.seq, lastSnapshotTs: msg.ts });
        store.applyMachineInventoryDelta(changed, removed, alertsFull, alertIds, services, rootBuses);
        store.setRootServiceFlow(msg.pairs as Pair[] | undefined, msg.pipelines as Pipeline[] | undefined);
        if (Array.isArray(msg.schedules)) store.setSchedules(msg.schedules as ScheduleSummary[]);
        setConnectionState('live');
        reconcileChildSockets(useConnectionStore.getState().machines);
        return;
      }

      if (msg.type === 'alert') {
        const alert = msg as unknown as AlertMessage;
        const nextRoot = { ...store.rootAlerts, [alert.alertId]: alert };
        useConnectionStore.setState({ rootAlerts: nextRoot });
        return;
      }
    }

    function connectRoot() {
      setConnectionState(rootWsRef.current ? 'reconnecting' : 'connecting');
      try {
        const ws = new WebSocket(withSessionToken(RELAY_WS_URL));
        rootWsRef.current = ws;

        ws.onopen = () => {
          rootBackoffRef.current = RECONNECT_MIN_MS;
          ws.send(JSON.stringify({ type: 'hello', device, clientId }));
        };

        ws.onmessage = (event) => handleRootMessage(String(event.data));

        ws.onerror = () => {
          showError('Relay connection error');
        };

        ws.onclose = (event) => {
          if (event.code === 4401) void useAuthStore.getState().expireIfRevoked();
          setConnectionState('offline');
          scheduleRootReconnect();
        };
      } catch {
        setConnectionState('offline');
        scheduleRootReconnect();
      }
    }

    connectRoot();

    return () => {
      closedByUsRef.current = true;
      if (rootReconnectTimer.current) clearTimeout(rootReconnectTimer.current);
      rootWsRef.current?.close();
      rootWsRef.current = null;
      for (const idStr of Object.keys(childSocketsRef.current)) closeChildSocket(Number(idStr));
    };
    // retryToken is a dependency on purpose: manualRetry() bumps it to re-run connectRoot().
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, clientId, device, retryToken]);

  function sendSilence(alertId: string) {
    if (!clientId) return;
    useConnectionStore.getState().silenceLocally(alertId, 15);
    const payload = JSON.stringify({ type: 'silence', alertId, clientId, device });
    rootWsRef.current?.send(payload);
    for (const ws of allChildWsRef.current) ws.send(payload);
  }

  function sendAck(alertId: string, pin: string) {
    if (FORCE_FIXTURE) {
      useConnectionStore.getState().applyAck({ type: 'ack', alertId, ackedBy: device, ackedAt: new Date().toISOString() });
      return;
    }
    const payload = JSON.stringify({ type: 'ack', alertId, ackedBy: device, pin });
    // Every socket verifies the PIN and counts failures, so a single socket must receive it.
    const root = rootWsRef.current;
    if (root && root.readyState === WebSocket.OPEN) {
      root.send(payload);
      return;
    }
    const machineId = useConnectionStore.getState().activeAlerts[alertId]?.machine_id;
    const own = machineId != null ? childSocketsRef.current[machineId]?.ws : null;
    const target = own && own.readyState === WebSocket.OPEN ? own : allChildWsRef.current.find((w) => w.readyState === WebSocket.OPEN);
    target?.send(payload);
  }

  useEffect(() => {
    if (!ready) return;
    setWsActions({ sendSilence, sendAck });
    return () => setWsActions(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, clientId, device]);

  return { sendSilence, sendAck };
}
