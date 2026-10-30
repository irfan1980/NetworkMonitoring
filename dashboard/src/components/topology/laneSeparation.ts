import type { PathPoint } from './lanePath';

export interface RouteSegment {
  id: string;
  points: PathPoint[];
}

const ANGLE_THRESHOLD_RAD = (6 * Math.PI) / 180;
const MIN_OVERLAP_FRACTION = 0.3;
const MIN_ABS_OVERLAP = 24;

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
  const lo = Math.max(aMin, bMin);
  const hi = Math.min(aMax, bMax);
  const overlap = hi - lo;
  const shorterLen = Math.min(aMax - aMin, bMax - bMin);
  if (shorterLen < 1e-6) return false;
  if (overlap < MIN_ABS_OVERLAP && overlap < shorterLen * MIN_OVERLAP_FRACTION) return false;
  const sign = projA2 >= projA1 ? 1 : -1;
  const t1 = (lo - projA1) / (sign * len);
  const t2 = (hi - projA1) / (sign * len);
  const p1x = ax1 + dx * t1;
  const p1y = ay1 + dy * t1;
  const p2x = ax1 + dx * t2;
  const p2y = ay1 + dy * t2;
  const d1 = pointToLineDist(p1x, p1y, bx1, by1, bx2, by2);
  const d2 = pointToLineDist(p2x, p2y, bx1, by1, bx2, by2);
  return Math.max(d1, d2) < distThreshold;
}

export function findCoincidentLanePairs(routes: RouteSegment[], distThreshold = 6): Array<[string, string]> {
  const segs: Array<{ id: string; x1: number; y1: number; x2: number; y2: number; angle: number }> = [];
  for (const route of routes) {
    for (let i = 0; i < route.points.length - 1; i++) {
      const a = route.points[i];
      const b = route.points[i + 1];
      segs.push({ id: route.id, x1: a.x, y1: a.y, x2: b.x, y2: b.y, angle: Math.atan2(b.y - a.y, b.x - a.x) });
    }
  }
  const seen = new Set<string>();
  const out: Array<[string, string]> = [];
  for (let i = 0; i < segs.length; i++) {
    for (let j = i + 1; j < segs.length; j++) {
      const s1 = segs[i];
      const s2 = segs[j];
      if (s1.id === s2.id) continue;
      if (undirectedAngleDiff(s1.angle, s2.angle) > ANGLE_THRESHOLD_RAD) continue;
      if (!segmentsCoincide(s1.x1, s1.y1, s1.x2, s1.y2, s2.x1, s2.y1, s2.x2, s2.y2, distThreshold)) continue;
      const [idA, idB] = s1.id < s2.id ? [s1.id, s2.id] : [s2.id, s1.id];
      const key = `${idA}|${idB}`;
      if (seen.has(key)) continue;
      seen.add(key);
      out.push([idA, idB]);
    }
  }
  return out;
}

function hashId(id: string): number {
  let h = 0;
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0;
  return h;
}

export function resolveLaneSeparation(
  laneIds: string[],
  buildRoute: (id: string, extraOffset: number) => PathPoint[],
  unit: number,
  isClear: (id: string, points: PathPoint[]) => boolean = () => true,
  maxPasses = 10
): { extra: Record<string, number>; remaining: number } {
  const extra: Record<string, number> = {};
  let remaining = 0;
  for (let pass = 0; pass < maxPasses; pass++) {
    const routes: RouteSegment[] = laneIds.map((id) => ({ id, points: buildRoute(id, extra[id] ?? 0) }));
    const violations = findCoincidentLanePairs(routes);
    remaining = violations.length;
    if (remaining === 0) break;
    let moved = false;
    for (const [idA, idB] of violations) {
      const loser = idA < idB ? idB : idA;
      const pref = hashId(loser) % 2 === 0 ? 1 : -1;
      outer: for (const k of [1, 2, 3]) {
        for (const sign of [pref, -pref]) {
          const next = (extra[loser] ?? 0) + sign * unit * k;
          if (isClear(loser, buildRoute(loser, next))) {
            extra[loser] = next;
            moved = true;
            break outer;
          }
        }
      }
    }
    if (!moved) break;
  }
  return { extra, remaining };
}
