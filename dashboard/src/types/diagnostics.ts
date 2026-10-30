export interface LastError {
  at: string;
  message: string;
  messageId: string;
}

export interface EntityDiagnostic {
  entity: string;
  pipelineId: string;
  senderFunction: string | null;
  senderFormat: string | null;
  receiverProcedure: string | null;
  receiverFormat: string | null;
  operations: string[];
  severity: number | null;
  pending: number;
  sentInWindow: number;
  appliedInWindow: number;
  errors: number;
  lastSentAt: string | null;
  lastAppliedAt: string | null;
  lastError: LastError | null;
  queued: number;
}

export type PairSideState = { available: true } | { available: false; reason: string; detail?: string };

export type PairBusState =
  | { available: true }
  | { available: false; reason: 'bus_down'; buses?: { id: string; name: string; blockedBy: string | null }[] }
  | { available: false; reason: 'bus_status_unknown' };

export interface PairEntitiesResponse {
  from: string;
  to: string;
  window: { seconds: number };
  entities: EntityDiagnostic[];
  sides?: { from: PairSideState; to: PairSideState };
  bus?: PairBusState;
}

export interface PlatformEntitiesGroup {
  to?: string;
  from?: string;
  entities: EntityDiagnostic[];
}

export interface PlatformEntitiesResponse {
  platform: string;
  outgoing: Array<{ to: string; entities: EntityDiagnostic[] }>;
  incoming: Array<{ from: string; entities: EntityDiagnostic[] }>;
}

export type BusDiagnostic = {
  id: string;
  name: string;
  ok: boolean;
  blockedBy: string | null;
  topics?: string[];
  usedBy: string[];
  label?: string;
};

export type BusesResponse = BusDiagnostic[];
