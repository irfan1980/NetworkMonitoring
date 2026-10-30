export type SlotAssignmentMode = 'canonical' | 'random';

export interface MachineGroupInput {
  machineId: number | null;
  platformIds: string[];
}

export interface SlotAssignment {
  mode: SlotAssignmentMode;
  machineOrder: (number | null)[];
  serviceOrder: Record<string, string[]>;
  orientation: Record<string, 'row' | 'column'>;
  rowWidthMult: number;
  gapTierOffset: number;
}

function mulberry32(seed: number): () => number {
  let s = seed >>> 0;
  return () => {
    s = (s + 0x6d2b79f5) | 0;
    let t = Math.imul(s ^ (s >>> 15), 1 | s);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function seededShuffle<T>(arr: T[], seed: number): T[] {
  const rand = mulberry32(seed);
  const out = [...arr];
  for (let i = out.length - 1; i > 0; i--) {
    const j = Math.floor(rand() * (i + 1));
    [out[i], out[j]] = [out[j], out[i]];
  }
  return out;
}

export function idSetHash(ids: string[]): string {
  const sorted = [...ids].sort();
  let h = 0x811c9dc5;
  for (const id of sorted) {
    for (let i = 0; i < id.length; i++) {
      h ^= id.charCodeAt(i);
      h = Math.imul(h, 0x01000193);
    }
    h ^= 0x2c;
  }
  return `${(h >>> 0).toString(36)}-${sorted.length}`;
}

export function canonicalAssignment(groups: MachineGroupInput[]): SlotAssignment {
  const serviceOrder: Record<string, string[]> = {};
  for (const g of groups) serviceOrder[String(g.machineId)] = g.platformIds;
  return {
    mode: 'canonical',
    machineOrder: groups.map((g) => g.machineId),
    serviceOrder,
    orientation: {},
    rowWidthMult: 1,
    gapTierOffset: 0,
  };
}

export function randomAssignment(groups: MachineGroupInput[], seed: number): SlotAssignment {
  const rand = mulberry32(seed);
  const machineOrder = seededShuffle(
    groups.map((g) => g.machineId),
    seed
  );
  const serviceOrder: Record<string, string[]> = {};
  const orientation: Record<string, 'row' | 'column'> = {};
  for (const g of groups) {
    const key = String(g.machineId);
    serviceOrder[key] = g.machineId != null ? seededShuffle(g.platformIds, seed + g.machineId * 7919) : g.platformIds;
    if (g.machineId != null) orientation[key] = rand() < 0.5 ? 'row' : 'column';
  }
  return {
    mode: 'random',
    machineOrder,
    serviceOrder,
    orientation,
    rowWidthMult: 0.55 + rand() * 0.85,
    gapTierOffset: Math.floor(rand() * 4),
  };
}

export function reorderMachine(
  assignment: SlotAssignment,
  machineId: number | null,
  dropPoint: { x: number; y: number },
  otherBoxes: { machineId: number | null; x: number; y: number; w: number; h: number }[]
): SlotAssignment {
  if (otherBoxes.length === 0) return assignment;
  let best = otherBoxes[0];
  let bestDist = Infinity;
  for (const b of otherBoxes) {
    const cx = b.x + b.w / 2;
    const cy = b.y + b.h / 2;
    const d = Math.hypot(cx - dropPoint.x, cy - dropPoint.y);
    if (d < bestDist) {
      bestDist = d;
      best = b;
    }
  }
  const order = assignment.machineOrder.filter((id) => id !== machineId);
  let insertAt = order.indexOf(best.machineId);
  if (insertAt < 0) insertAt = order.length;
  const bestCx = best.x + best.w / 2;
  if (dropPoint.x > bestCx) insertAt += 1;
  order.splice(insertAt, 0, machineId);
  return { ...assignment, mode: 'random', machineOrder: order };
}

export function reorderService(
  assignment: SlotAssignment,
  machineId: number | null,
  platformId: string,
  dropAxisValue: number,
  orientation: 'row' | 'column',
  siblingAxisValue: (id: string) => number
): SlotAssignment {
  const key = String(machineId);
  const list = assignment.serviceOrder[key] ?? [];
  const others = list.filter((id) => id !== platformId);
  if (others.length === 0) return assignment;
  let insertAt = others.length;
  for (let i = 0; i < others.length; i++) {
    if (dropAxisValue < siblingAxisValue(others[i])) {
      insertAt = i;
      break;
    }
  }
  const newList = [...others];
  newList.splice(insertAt, 0, platformId);
  return { ...assignment, mode: 'random', serviceOrder: { ...assignment.serviceOrder, [key]: newList } };
}

export function assignmentMatches(assignment: SlotAssignment, groups: MachineGroupInput[]): boolean {
  const groupIds = new Set(groups.map((g) => g.machineId));
  const orderIds = new Set(assignment.machineOrder);
  if (groupIds.size !== orderIds.size) return false;
  for (const id of groupIds) if (!orderIds.has(id)) return false;
  for (const g of groups) {
    const stored = assignment.serviceOrder[String(g.machineId)];
    if (!stored) return false;
    const a = new Set(stored);
    const b = new Set(g.platformIds);
    if (a.size !== b.size) return false;
    for (const id of b) if (!a.has(id)) return false;
  }
  return true;
}
