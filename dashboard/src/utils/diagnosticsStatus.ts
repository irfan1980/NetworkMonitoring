import { colors } from '../theme/colors';
import { queueLevelForCount } from '../constants/queueThresholds';
import type { EntityDiagnostic } from '../types/diagnostics';

export type EntityDotState = 'green' | 'yellow' | 'degraded' | 'red' | 'grey';

export function entityDotState(entity: EntityDiagnostic, windowSeconds: number): EntityDotState {
  if (entity.errors > 0) return 'red';
  const level = queueLevelForCount(Math.max(entity.queued ?? 0, entity.pending ?? 0));
  if (level !== 'neutral') return level;
  if (entity.lastAppliedAt) {
    const ageSeconds = (Date.now() - new Date(entity.lastAppliedAt).getTime()) / 1000;
    if (ageSeconds <= windowSeconds) return 'green';
  }
  return 'grey';
}

export function entityDotColor(state: EntityDotState): string {
  switch (state) {
    case 'green':
      return colors.good;
    case 'yellow':
      return colors.warn;
    case 'degraded':
      return colors.degraded;
    case 'red':
      return colors.bad;
    default:
      return colors.unknown;
  }
}

export function troubleshootHint(
  entity: EntityDiagnostic,
  fromLabel: string,
  toLabel: string
): string | null {
  if (entity.errors > 0) {
    return `If this stopped, check ${entity.receiverProcedure} on ${toLabel}.`;
  }
  if (entity.pending > 0 && !entity.lastSentAt) {
    return `If this stopped, check ${entity.senderFunction} on ${fromLabel}.`;
  }
  return null;
}
