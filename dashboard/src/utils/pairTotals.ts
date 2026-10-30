import { isQueuedUnknown } from './health';
import type { Pair, Pipeline, TopologyNode } from '../types/relay';

export function receiverBacklogFor(
  toPlatform: string,
  topologyNodes: Record<string, TopologyNode>,
  pipelines: Record<string, Pipeline>,
  fromPlatform?: string | null
): number {
  const node = topologyNodes[toPlatform];
  const incoming = node?.pipelines?.map((id) => pipelines[String(id)]).filter((p) => p?.direction === 'incoming') ?? [];
  const match = (fromPlatform ? incoming.find((p) => String(p.sourceSystem) === String(fromPlatform)) : undefined) ?? incoming[0];
  if (!match) return 0;
  return (match.pendingInbox ?? 0) + (match.unresolvedInbox ?? 0);
}

export interface PairTotals {
  pending: number;
  queued: number;
  queuedUnknown: boolean;
  lost: number;
  receiverBacklog: number;
  failed: number;
}

export function wirePairTotals(
  pair: Pair | undefined,
  from: string | null,
  to: string | null,
  topologyNodes: Record<string, TopologyNode>,
  pipelines: Record<string, Pipeline>
): PairTotals {
  return {
    pending: pair?.pendingOutbox ?? 0,
    queued: pair?.queued ?? 0,
    queuedUnknown: isQueuedUnknown(pair),
    lost: pair?.lost ?? 0,
    receiverBacklog: to ? receiverBacklogFor(to, topologyNodes, pipelines, from) : 0,
    failed: (pair?.failedOutbox ?? 0) + (pair?.failedInbox ?? 0),
  };
}
