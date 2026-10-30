export type HealthState =
  | 'Disabled'
  | 'Blocked'
  | 'Unknown'
  | 'Down'
  | 'Degraded'
  | 'Healthy';

export type ObjectStatus = 'ok' | 'down' | 'blocked' | 'unknown';
export type NodeStatus = 'ok' | 'grey';
export type EdgeLevel = 'green' | 'yellow' | 'red' | 'grey';
export type Platform = string;
export type RelayEnd = 'sender' | 'receiver';

export type PipelineId = string | number;

export interface QueueThresholds {
  yellow: number;
  degraded: number;
  red: number;
}

export interface PlatformDef {
  id: string;
  label: string;
  type: string;
  usedBuses?: string[];
}

export interface BusEntry {
  id: string;
  name: string;
  ok: boolean | null;
  blockedBy: string | null;
  topics?: string[];
  detail?: string;
  usedBy?: string[];
  label?: string;
  since?: string | null;
}

export interface Pipeline {
  pipelineId: PipelineId;
  pipelineNo?: number;
  name: string;
  sourceSystem: string;
  targetSystem: string;
  entityType: string;
  direction: 'incoming' | 'outgoing';
  queueOrTopic: string;
  isEnabled: boolean;
  owner: string;
  health: HealthState;
  heartbeatAgeSeconds: number | null;
  pendingOutbox: number;
  failedOutbox: number;
  unresolvedInbox: number;
  displayName?: string;
  pendingInbox?: number;
  oldestPendingOutboxSeconds?: number | null;
  oldestPendingInboxSeconds?: number | null;
  failedInbox?: number;
  sendLagSeconds: number | null;
  applyLagSeconds: number | null;
  e2eLagSeconds: number | null;
  minSeverity: number;
  blockedBy: string | null;
  lastPingAt: string | null;
  metricsStale?: boolean;
  metricsAsOf?: string | null;
  metricsAgeSeconds?: number | null;
  degradedBy?: string | null;
  queuedCount?: number | null;
  lostCount?: number | null;
  cross_machine?: CrossMachineAnnotation | null;
}

export interface MonitoredObject {
  id: string;
  kind: string;
  status: ObjectStatus;
  blockedBy: string | null;
  lastCheckedAt?: string;
  latencyMs?: number | null;
  detail: string | null;
  label?: string;
  since?: string | null;
}

export interface TopologyNode {
  id: string;
  kind: 'relay' | 'service_bus' | 'platform';
  platform?: Platform;
  end?: RelayEnd;
  status: NodeStatus;
  lastPingAt?: string | null;
  detail: string | null;
  label?: string;
  dbOk?: boolean;
  senderOk?: boolean;
  receiverOk?: boolean;
  senderHeartbeatAge?: number | null;
  receiverHeartbeatAge?: number | null;
  pendingCount?: number | null;
  senderPendingCount?: number | null;
  receiverPendingCount?: number | null;
  pipelines?: PipelineId[];
  issues?: string[];
  since?: string | null;
  conditionSince?: string | null;
  conditionObjectId?: string | null;
  heartbeatReadable?: boolean;
  blockedBy?: string | null;
  affects?: PipelineId[];
  affectsPipelineCount?: number | null;
  blockedPipelines?: number | null;
  metricsStale?: boolean;
  metricsAsOf?: string | null;
  metricsAgeSeconds?: number | null;
}

export interface ConfigIssueWhere {
  serverId?: string;
  busId?: string;
  pipelineId?: PipelineId;
  entity?: string;
  machineId?: string;
}

export interface ConfigIssue {
  code: string;
  severity: 'error' | 'warning';
  message: string;
  where?: ConfigIssueWhere;
}

export interface Pair {
  id: string;
  from: Platform;
  to: Platform;
  status: EdgeLevel;
  pendingOutbox: number;
  queued: number | null;
  queuedUnknown?: boolean;
  lost: number;
  lostSince?: string | null;
  packets: number;
  senderOk: boolean;
  receiverOk: boolean;
  blockedBy: string | null;
  failedOutbox?: number;
  failedInbox?: number;
  issues?: string[];
  bus?: string | null;
  busName?: string | null;
  warning?: string | null;
  missingEnd?: 'sender' | 'receiver' | null;
  missingEndReason?: 'disabled' | 'unconfigured' | null;
  since?: string | null;
  cross_machine?: CrossMachineAnnotation | null;
}

export interface BusStatus {
  ok: boolean;
  blockedBy: string | null;
}

export interface TopologyEdge {
  id: string;
  source?: string;
  target?: string;
  pipelineId?: PipelineId;
  end?: RelayEnd;
  pendingCount: number;
  level: EdgeLevel;
  queued: number;
  queuedUnknown?: boolean;
  lost?: number | null;
  since?: string | null;
}

export interface Topology {
  nodes: TopologyNode[];
  edges: TopologyEdge[];
}

export type AlertEvent = 'raised' | 'updated' | 'resolved';

export interface AlertMessage {
  type: 'alert';
  alertId: string;
  ts: string;
  event?: AlertEvent;
  resolvedAt?: string | null;
  rootCause: string | null;
  pipelineId: PipelineId | null;
  from: string;
  to: string;
  reason: string;
  detail?: string;
  severity: number;
  escalated: boolean;
  channels: string[];
  affects: string[];
  queuedCount: number;
  lostCount: number;
  since?: string | null;
  failedCount?: number | null;
  affectsPipelineCount?: number | null;
  affectsText?: string | null;
  kind?: string;
  schedule_id?: number;
  machine_id?: number | null;
  server_id?: number | string | null;
  acknowledged: boolean;
  acknowledged_by?: string | null;
  acknowledged_at?: string | null;
}

export type SummaryLevel = 'good' | 'attention' | 'problem' | 'offline' | 'outage';

export interface RelaySummary {
  level: SummaryLevel;
  headline: string;
  reason: string;
  lostTotal?: number;
  since?: string | null;
}

export interface SnapshotMessage {
  type: 'snapshot';
  source?: 'mock' | 'live';
  seq: number;
  ts: string;
  emulator: { healthy: boolean; lastCheckedAt: string; detail: string | null };
  pipelines: Pipeline[];
  objects: MonitoredObject[];
  topology: Topology;
  activeAlerts: AlertMessage[];
  summary?: RelaySummary;
  pairs?: Pair[];
  bus?: BusStatus | BusEntry[];
  machine_id?: number;
  active_services?: ActiveService[];
  platforms?: PlatformDef[];
  servers?: PlatformDef[];
  buses?: BusEntry[];
  configIssues?: ConfigIssue[];
  thresholds?: QueueThresholds;
}

export interface DeltaMessage {
  type: 'delta';
  seq: number;
  ts: string;
  emulator?: { healthy: boolean; detail?: string | null };
  pipelines?: Array<Partial<Pipeline> & { pipelineId: PipelineId }>;
  objects?: Array<Partial<MonitoredObject> & { id: string }>;
  topology?: {
    nodes?: Array<Partial<TopologyNode> & { id: string }>;
    edges?: Array<Partial<TopologyEdge> & { id: string }>;
    removed?: string[];
  };
  packets?: Array<{ pipelineId: PipelineId; count: number }>;
  activeAlerts?: string[];
  summary?: RelaySummary;
  pairs?: Array<Partial<Pair> & { id: string }>;
  pairsRemoved?: string[];
  removed?: {
    pipelines?: PipelineId[];
    pairs?: string[];
    topologyNodes?: string[];
    objects?: string[];
  };
  bus?: BusStatus | BusEntry[];
  machine_id?: number;
  active_services?: ActiveService[];
  platforms?: PlatformDef[];
  servers?: PlatformDef[];
  buses?: BusEntry[];
  configIssues?: ConfigIssue[];
  thresholds?: QueueThresholds;
}

export interface AckBroadcast {
  type: 'ack';
  alertId: string;
  ackedBy: string;
  ackedAt: string;
}

export interface AckResultMessage {
  type: 'ackResult';
  alertId: string;
  ok: boolean;
  error?: 'invalid_pin' | 'not_found' | string;
  reason?: 'invalid_pin' | 'rate_limited' | 'unknown_alert' | string | null;
}

export type RelayInMessage =
  | SnapshotMessage
  | DeltaMessage
  | AlertMessage
  | AckBroadcast
  | AckResultMessage;

export interface HelloMessage {
  type: 'hello';
  device: string;
  clientId: string;
}

export interface ResyncMessage {
  type: 'resync';
}

export interface SilenceMessage {
  type: 'silence';
  alertId: string;
  clientId: string;
  device: string;
}

export interface AckRequestMessage {
  type: 'ack';
  alertId: string;
  ackedBy: string;
  pin: string;
}

export type RelayOutMessage =
  | HelloMessage
  | ResyncMessage
  | SilenceMessage
  | AckRequestMessage;

export type ConnectionState = 'connecting' | 'live' | 'reconnecting' | 'offline' | 'unauthorized';

export type MachineStatus = 'pending' | 'online' | 'offline';

export interface MachinePing {
  rtt_ms: number;
}

export type MachineNodeLiveStatus = 'ok' | 'down' | 'unread';
export type MachineNodeStatus = MachineNodeLiveStatus | 'offline' | 'pending' | 'blocked' | 'unknown';
export type MachineNodeLastKnownStatus = MachineNodeLiveStatus | 'unknown';

export interface MachineNode {
  node_id: string;
  node_name: string;
  node_type: string;
  node_platform: string;
  server_id: number | string;
  status: MachineNodeStatus;
  last_known_status: MachineNodeLastKnownStatus;
  last_known_ts: string | null;
}

export interface MachineInfo {
  machine_id: number;
  machine_label_id?: string;
  machine_name: string;
  machine_type: string;
  machine_os: string;
  machine_ip: string;
  status: MachineStatus;
  last_probe_ts: string;
  last_active_ts: string | null;
  ping: MachinePing | null;
  node_count: number;
  platforms?: PlatformDef[];
  nodes?: MachineNode[];
  machine_scheduler?: boolean;
  scheduler_state?: SchedulerState;
  scheduler_reason?: string | null;
  scheduler_defaults?: SchedulerDefaults | null;
}

export interface SchedulerDefaults {
  timeout_s?: number | null;
  retries?: number | null;
  retry_delay_s?: number | null;
  run_if_missed?: boolean | null;
  fail_threshold?: number | null;
}

export type SchedulerState = 'up' | 'down' | 'auth_rejected' | 'off';

export type ScheduleHealth = 'green' | 'red' | 'grey';

export interface ScheduleSummary {
  id: number;
  name: string;
  machine_id: number;
  script: string;
  trigger_summary: string;
  next_run: string | null;
  last_run: string | null;
  last_outcome: string | null;
  consecutive_failures: number;
  fail_threshold: number;
  state: ScheduleHealth;
  running: boolean;
  reason?: string;
}

export interface StandaloneServiceNode {
  node_id: string;
  node_name: string;
  node_type: string;
  node_platform: string;
  server_id: number | string;
  status: MachineNodeStatus;
  last_known_status?: MachineNodeLastKnownStatus;
  last_known_ts?: string | null;
}

export interface MachineInventorySnapshotMessage {
  type: 'snapshot';
  source?: 'mock' | 'live';
  seq: number;
  ts: string;
  machines: MachineInfo[];
  activeAlerts?: AlertMessage[];
  buses?: BusEntry[];
  services?: StandaloneServiceNode[];
  schedules?: ScheduleSummary[];
  configIssues?: ConfigIssue[];
}

export interface MachineInventoryDeltaMessage {
  type: 'delta';
  source?: 'mock' | 'live';
  seq: number;
  ts: string;
  machines?: {
    changed?: Array<Partial<MachineInfo> & { machine_id: number }>;
    removed?: number[];
  };
  activeAlerts?: Array<string | AlertMessage>;
  buses?: BusEntry[];
  services?: StandaloneServiceNode[];
  schedules?: ScheduleSummary[];
}

export interface CrossMachineAnnotation {
  direction: 'to' | 'from';
  counterpart_machine_id: number;
  service_bus_id: number | string;
}

export interface ActiveService {
  node_id: string;
  node_name: string;
  node_type: string;
  node_platform?: string;
  service_bus_server_id?: string | number;
  status: 'pending' | 'ok' | 'down' | 'unknown';
  error_kind?: string | null;
  error?: string | null;
  last_probe_ts?: string | null;
}

export interface MachineScopedEnvelope {
  machine_id: number;
  active_services?: ActiveService[];
}

export type MachineSocketState =
  | 'connecting'
  | 'live'
  | 'reconnecting'
  | 'closed_offline'
  | 'unauthorized';
