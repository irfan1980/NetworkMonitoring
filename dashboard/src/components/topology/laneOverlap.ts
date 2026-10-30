export interface UnorderedEdge {
  a: string;
  b: string;
}

export function collectUnorderedEdges(pairs: Record<string, { from: string; to: string }>, ids: Set<string>): UnorderedEdge[] {
  const seen = new Set<string>();
  const out: UnorderedEdge[] = [];
  for (const p of Object.values(pairs)) {
    if (!p || p.from === p.to || !ids.has(p.from) || !ids.has(p.to)) continue;
    const key = p.from < p.to ? `${p.from}|${p.to}` : `${p.to}|${p.from}`;
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(p.from < p.to ? { a: p.from, b: p.to } : { a: p.to, b: p.from });
  }
  return out;
}

const ANGLE_THRESHOLD_RAD = (4 * Math.PI) / 180;
const MIN_OVERLAP_FRACTION = 0.5;

function normAngle(rad: number): number {
  let a = rad % Math.PI;
  if (a < 0) a += Math.PI;
  return a;
}

function undirectedAngleDiff(a: number, b: number): number {
  const d = Math.abs(normAngle(a) - normAngle(b));
  return Math.min(d, Math.PI - d);
}

function pointToLineDist(px: number, py: number, x1: number, y1: number, x2: number, y2: number): number {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len = Math.hypot(dx, dy);
  if (len < 1e-6) return Math.hypot(px - x1, py - y1);
  return Math.abs((px - x1) * dy - (py - y1) * dx) / len;
}

function segmentsCoincide(
  ax1: number,
  ay1: number,
  ax2: number,
  ay2: number,
  bx1: number,
  by1: number,
  bx2: number,
  by2: number,
  distThreshold: number
): boolean {
  const dx = ax2 - ax1;
  const dy = ay2 - ay1;
  const len = Math.hypot(dx, dy);
  if (len < 1e-6) return false;
  const ux = dx / len;
  const uy = dy / len;
  const projA1 = ax1 * ux + ay1 * uy;
  const projA2 = ax2 * ux + ay2 * uy;
  const projB1 = bx1 * ux + by1 * uy;
  const projB2 = bx2 * ux + by2 * uy;
  const aMin = Math.min(projA1, projA2);
  const aMax = Math.max(projA1, projA2);
  const bMin = Math.min(projB1, projB2);
  const bMax = Math.max(projB1, projB2);
  const overlap = Math.min(aMax, bMax) - Math.max(aMin, bMin);
  const shorterLen = Math.min(aMax - aMin, bMax - bMin);
  if (shorterLen < 1e-6 || overlap < shorterLen * MIN_OVERLAP_FRACTION) return false;

  const d1 = pointToLineDist(ax1, ay1, bx1, by1, bx2, by2);
  const d2 = pointToLineDist(ax2, ay2, bx1, by1, bx2, by2);
  return Math.max(d1, d2) < distThreshold;
}

export function countCoincidentPairs(edges: UnorderedEdge[], positions: Record<string, { x: number; y: number }>, distThreshold: number): number {
  const segs = edges
    .map((e) => {
      const p1 = positions[e.a];
      const p2 = positions[e.b];
      if (!p1 || !p2) return null;
      return { x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y, angle: Math.atan2(p2.y - p1.y, p2.x - p1.x) };
    })
    .filter((s): s is NonNullable<typeof s> => !!s);
  let count = 0;
  for (let i = 0; i < segs.length; i++) {
    for (let j = i + 1; j < segs.length; j++) {
      if (undirectedAngleDiff(segs[i].angle, segs[j].angle) > ANGLE_THRESHOLD_RAD) continue;
      if (segmentsCoincide(segs[i].x1, segs[i].y1, segs[i].x2, segs[i].y2, segs[j].x1, segs[j].y1, segs[j].x2, segs[j].y2, distThreshold)) count++;
    }
  }
  return count;
}
