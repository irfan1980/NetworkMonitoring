import { colors } from '../theme/colors';
import { useConnectionStore } from '../stores/connectionStore';
import type { QueueThresholds } from '../types/relay';

export const DEFAULT_QUEUE_THRESHOLDS: QueueThresholds = {
  yellow: 50,
  degraded: 100,
  red: 500,
};

export function getQueueThresholds(): QueueThresholds {
  return useConnectionStore.getState().thresholds ?? DEFAULT_QUEUE_THRESHOLDS;
}

export type QueueLevel = 'neutral' | 'yellow' | 'degraded' | 'red';

export function queueLevelForCount(count: number | null | undefined, t: QueueThresholds = getQueueThresholds()): QueueLevel {
  if (count == null || !Number.isFinite(count) || count <= 0) return 'neutral';
  if (count >= t.red) return 'red';
  if (count >= t.degraded) return 'degraded';
  if (count >= t.yellow) return 'yellow';
  return 'neutral';
}

const LEVEL_COLOR: Record<QueueLevel, string> = {
  neutral: colors.good,
  yellow: colors.warn,
  degraded: colors.degraded,
  red: colors.bad,
};

const LEVEL_DIM: Record<QueueLevel, string> = {
  neutral: colors.goodDim,
  yellow: colors.warnDim,
  degraded: colors.degradedDim,
  red: colors.badDim,
};

export function countColor(count: number | null | undefined, neutralColor: string = colors.good): string {
  const level = queueLevelForCount(count);
  return level === 'neutral' ? neutralColor : LEVEL_COLOR[level];
}

export function countDimColor(count: number | null | undefined, neutralDim: string = colors.goodDim): string {
  const level = queueLevelForCount(count);
  return level === 'neutral' ? neutralDim : LEVEL_DIM[level];
}

export function queueLevelLabel(level: QueueLevel, t: QueueThresholds = getQueueThresholds()): string {
  switch (level) {
    case 'red':
      return `Red = ${t.red}+ waiting`;
    case 'degraded':
      return `Orange = ${t.degraded}+ waiting (Degraded)`;
    case 'yellow':
      return `Yellow = ${t.yellow}+ waiting`;
    default:
      return 'Green = flowing';
  }
}
