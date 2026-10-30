import { computeLineLayout, computeRowLayout } from './gridLayout';
import { defaultBypassMargin } from './lanePath';

export interface MachineLayoutGroup {
  machineId: number | null;
  platformIds: string[];
  orientation?: 'column' | 'row';
}

export interface MachineLayoutOptions {
  nodeRadius: number;
  gap: number;
  boxPadding: number;
  headerHeight: number;
  gutter: number;
  targetRowWidth: number;
  placeholderSize: { w: number; h: number };
}

export interface MachineLayoutBox {
  machineId: number | null;
  x: number;
  y: number;
  w: number;
  h: number;
  placeholder: boolean;
}

export interface MachineLayoutResult {
  positions: Record<string, { x: number; y: number }>;
  boxes: MachineLayoutBox[];
  worldWidth: number;
  worldHeight: number;
}

interface SizedBox {
  machineId: number | null;
  w: number;
  h: number;
  placeholder: boolean;
  localPositions: Record<string, { x: number; y: number }>;
}

function sizeGroup(group: MachineLayoutGroup, opts: MachineLayoutOptions): SizedBox {
  const { nodeRadius, gap, boxPadding, headerHeight, placeholderSize } = opts;
  if (group.platformIds.length === 0) {
    return { machineId: group.machineId, w: placeholderSize.w, h: placeholderSize.h, placeholder: true, localPositions: {} };
  }
  const mini =
    group.orientation === 'row'
      ? computeRowLayout(group.platformIds, { nodeRadius, gap })
      : computeLineLayout(group.platformIds, { nodeRadius, gap });
  const bypassRoom = group.platformIds.length >= 3 ? defaultBypassMargin(nodeRadius) + nodeRadius * 0.6 : 0;
  const extraW = group.orientation === 'row' ? 0 : bypassRoom;
  const extraH = group.orientation === 'row' ? bypassRoom : 0;
  const w = Math.max(mini.worldWidth, nodeRadius * 2) + boxPadding * 2 + extraW;
  const h = Math.max(mini.worldHeight, nodeRadius * 2) + headerHeight + boxPadding * 2 + extraH;
  const padX = boxPadding + extraW / 2;
  const padY = headerHeight + boxPadding + extraH / 2;
  const localPositions: Record<string, { x: number; y: number }> = {};
  for (const [id, p] of Object.entries(mini.positions)) {
    localPositions[id] = { x: p.x + padX, y: p.y + padY };
  }
  return { machineId: group.machineId, w, h, placeholder: false, localPositions };
}

function packShelves(boxes: SizedBox[], targetRowWidth: number, gutter: number): MachineLayoutResult {
  let x = 0;
  let y = 0;
  let rowHeight = 0;
  let maxRowRight = 0;
  const placed: MachineLayoutBox[] = [];
  const positions: Record<string, { x: number; y: number }> = {};
  for (const box of boxes) {
    if (x > 0 && x + box.w > Math.max(targetRowWidth, box.w)) {
      x = 0;
      y += rowHeight + gutter;
      rowHeight = 0;
    }
    placed.push({ machineId: box.machineId, x, y, w: box.w, h: box.h, placeholder: box.placeholder });
    for (const [id, p] of Object.entries(box.localPositions)) {
      positions[id] = { x: x + p.x, y: y + p.y };
    }
    x += box.w + gutter;
    rowHeight = Math.max(rowHeight, box.h);
    maxRowRight = Math.max(maxRowRight, x - gutter);
  }
  return { positions, boxes: placed, worldWidth: Math.max(maxRowRight, 1), worldHeight: y + rowHeight };
}

export function computeMachineLayout(groups: MachineLayoutGroup[], opts: MachineLayoutOptions): MachineLayoutResult {
  if (groups.length === 0) return { positions: {}, boxes: [], worldWidth: 0, worldHeight: 0 };
  const sized = groups.map((g) => sizeGroup(g, opts));
  return packShelves(sized, opts.targetRowWidth, opts.gutter);
}
