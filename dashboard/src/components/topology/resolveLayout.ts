import { computeMachineLayout, type MachineLayoutGroup, type MachineLayoutResult } from './machineLayout';
import { randomAssignment, type MachineGroupInput, type SlotAssignment, type SlotAssignmentMode } from './arrangement';
import { countCoincidentPairs, type UnorderedEdge } from './laneOverlap';

export const GAP_GUTTER_TIERS: Array<{ gapMult: number; gutterMult: number }> = [
  { gapMult: 3, gutterMult: 1.8 },
  { gapMult: 2.4, gutterMult: 1.5 },
  { gapMult: 1.8, gutterMult: 1.2 },
  { gapMult: 1.4, gutterMult: 1.0 },
];

export function buildGroupsForLayout(rawGroups: MachineGroupInput[], assignment: SlotAssignment): MachineLayoutGroup[] {
  if (rawGroups.length === 0) return [];
  const byId = new Map(rawGroups.map((g) => [g.machineId, g] as const));
  const ordered: MachineLayoutGroup[] = [];
  const seen = new Set<number | null>();
  for (const mid of assignment.machineOrder) {
    const base = byId.get(mid);
    if (!base || seen.has(mid)) continue;
    seen.add(mid);
    const stored = assignment.serviceOrder[String(mid)];
    const validStored = stored && stored.length === base.platformIds.length && stored.every((id) => base.platformIds.includes(id));
    ordered.push({
      machineId: mid,
      platformIds: validStored ? stored : base.platformIds,
      orientation: mid != null ? assignment.orientation[String(mid)] : undefined,
    });
  }
  for (const g of rawGroups) if (!seen.has(g.machineId)) ordered.push(g);
  return ordered;
}

export interface ResolveLayoutInput {
  groups: MachineLayoutGroup[];
  mode: SlotAssignmentMode;
  rowWidthMult: number;
  gapTierOffset: number;
  machineRowEligible: Map<number, boolean>;
  r: number;
  width: number;
  height: number;
  edges: UnorderedEdge[];
}

export interface ResolvedLayout {
  layout: MachineLayoutResult;
  overlapCount: number;
}

export function resolveMachineLayout(input: ResolveLayoutInput): ResolvedLayout | null {
  const { groups, mode, rowWidthMult, gapTierOffset, machineRowEligible, r, width, height, edges } = input;
  if (groups.length === 0) return null;

  const targetRowWidth = Math.max(width, r * 3) * rowWidthMult;
  const baseOptions = {
    nodeRadius: r,
    boxPadding: 26,
    headerHeight: 44,
    targetRowWidth,
    placeholderSize: { w: 176, h: 102 },
  };
  const fitScoreOf = (c: MachineLayoutResult) => Math.min(width / Math.max(1, c.worldWidth), height / Math.max(1, c.worldHeight));
  const aspectScoreOf = (c: MachineLayoutResult) => {
    const worldAspect = Math.max(1, c.worldWidth) / Math.max(1, c.worldHeight);
    const viewportAspect = Math.max(1, width) / Math.max(1, height);
    return -Math.abs(Math.log(worldAspect / viewportAspect));
  };
  const distThreshold = 10;
  const overlapsOf = (c: MachineLayoutResult) => countCoincidentPairs(edges, c.positions, distThreshold);

  const useAssignedOrientation = mode === 'random';
  const rowVariantGroups: MachineLayoutGroup[] = useAssignedOrientation
    ? groups
    : groups.map((g) => (g.machineId == null || !machineRowEligible.get(g.machineId) ? g : { ...g, orientation: 'row' as const }));
  const hasRowCandidate = useAssignedOrientation || rowVariantGroups.some((g) => g.orientation === 'row');

  const norm = ((gapTierOffset % GAP_GUTTER_TIERS.length) + GAP_GUTTER_TIERS.length) % GAP_GUTTER_TIERS.length;
  const tiers = norm > 0 ? [...GAP_GUTTER_TIERS.slice(norm), ...GAP_GUTTER_TIERS.slice(0, norm)] : GAP_GUTTER_TIERS;

  const pick = (a: MachineLayoutResult, b: MachineLayoutResult): MachineLayoutResult => {
    const oa = overlapsOf(a);
    const ob = overlapsOf(b);
    if (oa !== ob) return oa < ob ? a : b;
    const aa = aspectScoreOf(a);
    const ab = aspectScoreOf(b);
    if (Math.abs(aa - ab) > 1e-6) return aa > ab ? a : b;
    return fitScoreOf(a) > fitScoreOf(b) ? a : b;
  };

  let best: MachineLayoutResult | null = null;
  for (const { gapMult, gutterMult } of tiers) {
    const tierOptions = {
      ...baseOptions,
      gap: r * gapMult,
      gutter: Math.max(140, r * gutterMult),
    };
    const candidate = useAssignedOrientation
      ? computeMachineLayout(rowVariantGroups, tierOptions)
      : (() => {
          const columnCandidate = computeMachineLayout(groups, tierOptions);
          if (!hasRowCandidate) return columnCandidate;
          const rowCandidate = computeMachineLayout(rowVariantGroups, tierOptions);
          return pick(rowCandidate, columnCandidate);
        })();
    best = best ? pick(candidate, best) : candidate;
  }
  if (!best) return null;
  return { layout: best, overlapCount: overlapsOf(best) };
}

export interface CleanAssignmentInput {
  rawGroups: MachineGroupInput[];
  machineRowEligible: Map<number, boolean>;
  r: number;
  width: number;
  height: number;
  edges: UnorderedEdge[];
  seed: number;
  maxAttempts?: number;
}

export function pickCleanRandomAssignment(input: CleanAssignmentInput): SlotAssignment {
  const { rawGroups, machineRowEligible, r, width, height, edges, seed, maxAttempts = 8 } = input;
  const scoreOf = (assignment: SlotAssignment): number => {
    const groups = buildGroupsForLayout(rawGroups, assignment);
    const resolved = resolveMachineLayout({
      groups,
      mode: assignment.mode,
      rowWidthMult: assignment.rowWidthMult,
      gapTierOffset: assignment.gapTierOffset,
      machineRowEligible,
      r,
      width,
      height,
      edges,
    });
    return resolved?.overlapCount ?? 0;
  };
  let s = seed;
  let best = randomAssignment(rawGroups, s);
  let bestScore = scoreOf(best);
  for (let i = 1; i < maxAttempts && bestScore > 0; i++) {
    s = (s + 0x9e3779b9) >>> 0 || 1;
    const candidate = randomAssignment(rawGroups, s);
    const score = scoreOf(candidate);
    if (score < bestScore) {
      best = candidate;
      bestScore = score;
    }
    if (score === 0) return candidate;
  }
  return best;
}
