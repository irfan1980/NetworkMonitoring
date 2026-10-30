export interface GridLayoutOptions {
  nodeRadius: number;
  gap: number;
  aspect?: number;
}

export interface GridLayoutResult {
  positions: Record<string, { x: number; y: number }>;
  cols: number;
  rows: number;
  cellSize: number;
  worldWidth: number;
  worldHeight: number;
  nodeRadius: number;
}

const EMPTY: GridLayoutResult = {
  positions: {},
  cols: 0,
  rows: 0,
  cellSize: 0,
  worldWidth: 0,
  worldHeight: 0,
  nodeRadius: 0,
};

export function computeGridLayout(ids: string[], opts: GridLayoutOptions): GridLayoutResult {
  const n = ids.length;
  if (n === 0) return EMPTY;
  const cellSize = opts.nodeRadius * 2 + opts.gap;
  const aspect = opts.aspect ?? 1.6;
  const cols = Math.max(1, Math.round(Math.sqrt(n * aspect)));
  const rows = Math.max(1, Math.ceil(n / cols));
  const positions: Record<string, { x: number; y: number }> = {};
  ids.forEach((id, i) => {
    const col = i % cols;
    const row = Math.floor(i / cols);
    positions[id] = {
      x: cellSize / 2 + col * cellSize,
      y: cellSize / 2 + row * cellSize,
    };
  });
  return {
    positions,
    cols,
    rows,
    cellSize,
    worldWidth: cols * cellSize,
    worldHeight: rows * cellSize,
    nodeRadius: opts.nodeRadius,
  };
}

export function computeLineLayout(ids: string[], opts: GridLayoutOptions): GridLayoutResult {
  const n = ids.length;
  if (n === 0) return EMPTY;
  const cellSize = opts.nodeRadius * 2 + opts.gap;
  const positions: Record<string, { x: number; y: number }> = {};
  ids.forEach((id, i) => {
    positions[id] = { x: cellSize / 2, y: cellSize / 2 + i * cellSize };
  });
  return {
    positions,
    cols: 1,
    rows: n,
    cellSize,
    worldWidth: cellSize,
    worldHeight: n * cellSize,
    nodeRadius: opts.nodeRadius,
  };
}

export function computeRowLayout(ids: string[], opts: GridLayoutOptions): GridLayoutResult {
  const n = ids.length;
  if (n === 0) return EMPTY;
  const cellSize = opts.nodeRadius * 2 + opts.gap;
  const positions: Record<string, { x: number; y: number }> = {};
  ids.forEach((id, i) => {
    positions[id] = { x: cellSize / 2 + i * cellSize, y: cellSize / 2 };
  });
  return {
    positions,
    cols: n,
    rows: 1,
    cellSize,
    worldWidth: n * cellSize,
    worldHeight: cellSize,
    nodeRadius: opts.nodeRadius,
  };
}

export function assertNoOverlap(result: GridLayoutResult): { ok: boolean; violations: [string, string, number][] } {
  const entries = Object.entries(result.positions);
  const violations: [string, string, number][] = [];
  const minDist = result.nodeRadius * 2;
  for (let i = 0; i < entries.length; i++) {
    for (let j = i + 1; j < entries.length; j++) {
      const [idA, a] = entries[i];
      const [idB, b] = entries[j];
      const d = Math.hypot(a.x - b.x, a.y - b.y);
      if (d < minDist) violations.push([idA, idB, d]);
    }
  }
  return { ok: violations.length === 0, violations };
}
