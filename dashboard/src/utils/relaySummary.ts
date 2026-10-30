import type { RelaySummary, SummaryLevel } from '../types/relay';

const SUMMARY_LEVEL_RANK: Record<SummaryLevel, number> = {
  problem: 4,
  attention: 3,
  outage: 2,
  offline: 1,
  good: 0,
};

export function aggregateMachineSummaries(summaries: (RelaySummary | null | undefined)[]): RelaySummary | null {
  let worst: RelaySummary | null = null;
  for (const s of summaries) {
    if (!s) continue;
    if (!worst || SUMMARY_LEVEL_RANK[s.level] > SUMMARY_LEVEL_RANK[worst.level]) worst = s;
  }
  return worst;
}
