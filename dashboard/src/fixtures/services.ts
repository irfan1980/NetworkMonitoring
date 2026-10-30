import type { AlertMessage, Pair, Pipeline, StandaloneServiceNode } from '../types/relay';

export const FIXTURE_SERVICES: StandaloneServiceNode[] = [
  { node_id: 'abafar', node_name: 'MSSQL-Abafar-Dev', node_type: 'Database', node_platform: 'mssql', server_id: 9001, status: 'ok' },
  { node_id: 'mars', node_name: 'MSSQL-Mars-Dev', node_type: 'Database', node_platform: 'mssql', server_id: 9002, status: 'down' },
  { node_id: 'redis-cache', node_name: 'Redis', node_type: 'Cache', node_platform: 'redis', server_id: 9003, status: 'ok' },
];

const pair = (from: string, to: string, over: Partial<Pair> = {}): Pair => ({
  id: `${from}->${to}`,
  from,
  to,
  status: 'green',
  pendingOutbox: 0,
  queued: 0,
  lost: 0,
  packets: 2,
  senderOk: true,
  receiverOk: true,
  blockedBy: null,
  bus: 'sb-emulator-1',
  busName: 'microsoft-azure-servicebus-emulator',
  ...over,
});

const pipeline = (id: string, from: string, to: string, name: string): Pipeline =>
  ({
    pipelineId: id,
    name,
    sourceSystem: from,
    targetSystem: to,
    entityType: 'ORDER',
    direction: 'outgoing',
    queueOrTopic: 'sync',
    isEnabled: true,
    owner: null,
    health: 'Healthy',
    heartbeatAgeSeconds: 1,
    pendingOutbox: 0,
    failedOutbox: 0,
    unresolvedInbox: 0,
    pendingInbox: 0,
    sendLagSeconds: 1,
    applyLagSeconds: null,
    e2eLagSeconds: 4,
    minSeverity: 3,
    blockedBy: null,
    lastPingAt: '2026-09-17T09:05:50.000Z',
    queuedCount: 0,
    lostCount: 0,
  }) as unknown as Pipeline;

export const FIXTURE_SERVICE_PAIRS: Pair[] = [
  pair('9001', '9002', { status: 'grey', receiverOk: false, packets: 3 }),
  pair('9002', '9001', { status: 'grey', senderOk: false }),
  pair('9001', '9003'),
  pair('9001', '1003', { pendingOutbox: 5, status: 'yellow' }),
];

export const FIXTURE_SERVICE_PIPELINES: Pipeline[] = [
  pipeline('9001:1', '9001', '9002', 'abafar-to-mars'),
  pipeline('9002:2', '9002', '9001', 'mars-to-abafar'),
  pipeline('9001:3', '9001', '9003', 'abafar-to-redis'),
  pipeline('9001:4', '9001', '1003', 'abafar-to-sqlserver'),
];

export const FIXTURE_SERVICE_ALERT: AlertMessage = {
  type: 'alert',
  alertId: 'db-9002-query-down',
  ts: '2026-09-17T09:05:50.000Z',
  since: '2026-09-17T09:04:10.000Z',
  event: 'raised',
  resolvedAt: null,
  rootCause: 'object:db:mssql:9002:query',
  server_id: 9002,
  machine_id: null,
  pipelineId: null,
  from: 'ok',
  to: 'down',
  reason: 'MSSQL-Mars-Dev (server 9002): Login failed for user (18456)',
  severity: 2,
  escalated: false,
  channels: [],
  affects: [],
  queuedCount: 0,
  lostCount: 0,
  acknowledged: false,
};
