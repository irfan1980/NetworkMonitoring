export interface PathPoint {
  x: number;
  y: number;
}

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export function buildPath(x1: number, y1: number, x2: number, y2: number, via?: PathPoint[]): PathPoint[] {
  return via && via.length > 0 ? [{ x: x1, y: y1 }, ...via, { x: x2, y: y2 }] : [{ x: x1, y: y1 }, { x: x2, y: y2 }];
}

export function pathMeta(path: PathPoint[]): { segLens: number[]; total: number; cum: number[] } {
  const segLens: number[] = [];
  let total = 0;
  for (let i = 0; i < path.length - 1; i++) {
    const d = Math.hypot(path[i + 1].x - path[i].x, path[i + 1].y - path[i].y);
    segLens.push(d);
    total += d;
  }
  const safeTotal = Math.max(1, total);
  const cum: number[] = [0];
  let running = 0;
  for (const d of segLens) {
    running += d;
    cum.push(running / safeTotal);
  }
  return { segLens, total: safeTotal, cum };
}

export function pointAtPath(path: PathPoint[], cum: number[], t: number): PathPoint {
  'worklet';
  const tt = t < 0 ? 0 : t > 1 ? 1 : t;
  if (path.length === 2) {
    return { x: path[0].x + (path[1].x - path[0].x) * tt, y: path[0].y + (path[1].y - path[0].y) * tt };
  }
  for (let i = 0; i < cum.length - 1; i++) {
    if (tt <= cum[i + 1] || i === cum.length - 2) {
      const span = cum[i + 1] - cum[i];
      const segT = span > 0 ? (tt - cum[i]) / span : 0;
      const a = path[i];
      const b = path[i + 1];
      return { x: a.x + (b.x - a.x) * segT, y: a.y + (b.y - a.y) * segT };
    }
  }
  return path[path.length - 1];
}

export function tangentAtPath(path: PathPoint[], cum: number[], t: number): { ux: number; uy: number } {
  'worklet';
  const tt = t < 0 ? 0 : t > 1 ? 1 : t;
  for (let i = 0; i < cum.length - 1; i++) {
    if (tt <= cum[i + 1] || i === cum.length - 2) {
      const a = path[i];
      const b = path[i + 1];
      const dx = b.x - a.x;
      const dy = b.y - a.y;
      const len = Math.max(1, Math.hypot(dx, dy));
      return { ux: dx / len, uy: dy / len };
    }
  }
  const last = path.length - 1;
  const dx = path[last].x - path[last - 1].x;
  const dy = path[last].y - path[last - 1].y;
  const len = Math.max(1, Math.hypot(dx, dy));
  return { ux: dx / len, uy: dy / len };
}

export function subPathPoints(path: PathPoint[], cum: number[], startT: number, endT: number): PathPoint[] {
  const out: PathPoint[] = [pointAtPath(path, cum, startT)];
  for (let i = 1; i < path.length - 1; i++) {
    if (cum[i] > startT && cum[i] < endT) out.push(path[i]);
  }
  out.push(pointAtPath(path, cum, endT));
  return out;
}

export function pointsToSvg(points: PathPoint[]): string {
  return points.map((p) => `${p.x},${p.y}`).join(' ');
}

export function segIntersectsRect(a: PathPoint, b: PathPoint, rect: Rect, pad: number): boolean {
  const rx0 = rect.x - pad;
  const ry0 = rect.y - pad;
  const rx1 = rect.x + rect.w + pad;
  const ry1 = rect.y + rect.h + pad;
  if ((a.x >= rx0 && a.x <= rx1 && a.y >= ry0 && a.y <= ry1) || (b.x >= rx0 && b.x <= rx1 && b.y >= ry0 && b.y <= ry1)) return true;
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  let t0 = 0;
  let t1 = 1;
  const clip = (p: number, q: number): boolean => {
    if (p === 0) return q >= 0;
    const r = q / p;
    if (p < 0) {
      if (r > t1) return false;
      if (r > t0) t0 = r;
    } else {
      if (r < t0) return false;
      if (r < t1) t1 = r;
    }
    return true;
  };
  if (!clip(-dx, a.x - rx0)) return false;
  if (!clip(dx, rx1 - a.x)) return false;
  if (!clip(-dy, a.y - ry0)) return false;
  if (!clip(dy, ry1 - a.y)) return false;
  return t0 <= t1;
}

export function defaultBypassMargin(nodeRadius: number): number {
  return Math.max(24, nodeRadius * 0.35);
}

export function computeBypassPath(
  p1: PathPoint,
  p2: PathPoint,
  otherBoxes: Rect[],
  margin = 24,
  stagger = 0,
  bounds?: Rect | null
): PathPoint[] | null {
  if (otherBoxes.length === 0) return null;
  const directBlockers = otherBoxes.filter((box) => segIntersectsRect(p1, p2, box, margin * 0.4));
  if (directBlockers.length === 0) return null;

  const dx = p2.x - p1.x;
  const dy = p2.y - p1.y;
  const vertical = Math.abs(dy) > Math.abs(dx);
  const rawExtra = Math.max(0, stagger);

  const insetPad = Math.max(4, margin * 0.3);
  const boundsX = bounds ? { min: bounds.x + insetPad, max: bounds.x + bounds.w - insetPad } : null;
  const boundsY = bounds ? { min: bounds.y + insetPad, max: bounds.y + bounds.h - insetPad } : null;
  const extraFor = (axisRange: { min: number; max: number } | null): number => {
    if (rawExtra === 0) return 0;
    if (axisRange) {
      const room = Math.max(8, (axisRange.max - axisRange.min) * 0.16);
      return rawExtra % room;
    }
    // Cap the stagger: unbounded, it can sweep the approach leg across an unrelated machine box.
    const room = Math.max(8, margin * 4);
    return rawExtra % room;
  };

  function clamp(v: number, range: { min: number; max: number } | null): number {
    if (!range) return v;
    if (range.max < range.min) return (range.min + range.max) / 2;
    return Math.min(Math.max(v, range.min), range.max);
  }

  function buildSideStep(side: 'left' | 'right', blockers: Rect[]): PathPoint[] {
    const extra = extraFor(boundsX);
    const edge = side === 'left' ? Math.min(...blockers.map((b) => b.x)) - margin : Math.max(...blockers.map((b) => b.x + b.w)) + margin;
    const detourX = clamp(side === 'left' ? edge - extra : edge + extra, boundsX);
    return [
      { x: detourX, y: p1.y },
      { x: detourX, y: p2.y },
    ];
  }

  function buildHighway(side: 'above' | 'below', blockers: Rect[]): PathPoint[] | null {
    const extra = extraFor(boundsY);
    const blockerEdge = side === 'above' ? Math.min(...blockers.map((b) => b.y)) - margin : Math.max(...blockers.map((b) => b.y + b.h)) + margin;
    const endpointEdge = side === 'above' ? Math.min(p1.y, p2.y) - margin * 0.5 : Math.max(p1.y, p2.y) + margin * 0.5;
    const edge = side === 'above' ? Math.min(blockerEdge, endpointEdge) : Math.max(blockerEdge, endpointEdge);
    const laneY = clamp(side === 'above' ? edge - extra : edge + extra, boundsY);
    return [
      { x: p1.x, y: laneY },
      { x: p2.x, y: laneY },
    ];
  }

  function fullyClear(route: PathPoint[]): boolean {
    const full = [p1, ...route, p2];
    for (const box of otherBoxes) {
      for (let i = 0; i < full.length - 1; i++) {
        if (segIntersectsRect(full[i], full[i + 1], box, margin * 0.4)) return false;
      }
    }
    return true;
  }

  function routeAroundUnion(blockers: Rect[]): PathPoint[] | null {
    if (blockers.length === 0) return null;
    const minX = Math.min(...blockers.map((b) => b.x)) - margin;
    const maxX = Math.max(...blockers.map((b) => b.x + b.w)) + margin;
    const minY = Math.min(...blockers.map((b) => b.y)) - margin;
    const maxY = Math.max(...blockers.map((b) => b.y + b.h)) + margin;
    const corners = [
      { x: minX, y: minY },
      { x: maxX, y: minY },
      { x: minX, y: maxY },
      { x: maxX, y: maxY },
    ].map((c) => ({ x: clamp(c.x, boundsX), y: clamp(c.y, boundsY) }));
    let best: PathPoint[] | null = null;
    let bestLen = Infinity;
    for (const c of corners) {
      const candidates: PathPoint[][] = [
        [{ x: c.x, y: p1.y }, { x: c.x, y: p2.y }],
        [{ x: p1.x, y: c.y }, { x: p2.x, y: c.y }],
      ];
      for (const route of candidates) {
        if (!fullyClear(route)) continue;
        const len = pathLen(route);
        if (len < bestLen) {
          bestLen = len;
          best = route;
        }
      }
    }
    return best;
  }

  function refine(build: (blockers: Rect[]) => PathPoint[] | null): PathPoint[] | null {
    let blockers = directBlockers;
    for (let iter = 0; iter < 6; iter++) {
      const route = build(blockers);
      if (!route) return null;
      const full = [p1, ...route, p2];
      let grew = false;
      for (const box of otherBoxes) {
        if (blockers.includes(box)) continue;
        for (let i = 0; i < full.length - 1; i++) {
          if (segIntersectsRect(full[i], full[i + 1], box, margin * 0.4)) {
            blockers = [...blockers, box];
            grew = true;
            break;
          }
        }
        if (grew) break;
      }
      if (!grew) return fullyClear(route) ? route : (routeAroundUnion(blockers) ?? route);
    }
    const last = build(blockers);
    if (last && fullyClear(last)) return last;
    return routeAroundUnion(blockers) ?? last;
  }

  function pathLen(route: PathPoint[]): number {
    const full = [p1, ...route, p2];
    let total = 0;
    for (let i = 0; i < full.length - 1; i++) total += Math.hypot(full[i + 1].x - full[i].x, full[i + 1].y - full[i].y);
    return total;
  }

  function pickShorter(a: PathPoint[] | null, b: PathPoint[] | null): PathPoint[] | null {
    if (a && b) return pathLen(a) <= pathLen(b) ? a : b;
    return a ?? b;
  }

  const best = vertical
    ? pickShorter(refine((b) => buildSideStep('left', b)), refine((b) => buildSideStep('right', b)))
    : pickShorter(refine((b) => buildHighway('above', b)), refine((b) => buildHighway('below', b)));

  if (!bounds && (!best || !fullyClear(best))) {
    const graph = shortestClearRoute(p1, p2, otherBoxes, margin, extraFor(null));
    if (graph) return graph;
  }
  return best ?? null;
}

export function shortestClearRoute(p1: PathPoint, p2: PathPoint, boxes: Rect[], margin: number, extra = 0): PathPoint[] | null {
  const pad = margin * 0.4;
  const infl = margin + extra;
  const nodes: PathPoint[] = [p1, p2];
  for (const b of boxes) {
    for (const c of [
      { x: b.x - infl, y: b.y - infl },
      { x: b.x + b.w + infl, y: b.y - infl },
      { x: b.x - infl, y: b.y + b.h + infl },
      { x: b.x + b.w + infl, y: b.y + b.h + infl },
    ]) {
      if (boxes.some((o) => c.x > o.x - pad && c.x < o.x + o.w + pad && c.y > o.y - pad && c.y < o.y + o.h + pad)) continue;
      nodes.push(c);
    }
  }
  const clear = (a: PathPoint, b: PathPoint) => boxes.every((o) => !segIntersectsRect(a, b, o, pad));
  const n = nodes.length;
  const dist = new Array<number>(n).fill(Infinity);
  const prev = new Array<number>(n).fill(-1);
  const done = new Array<boolean>(n).fill(false);
  dist[0] = 0;
  for (let it = 0; it < n; it++) {
    let u = -1;
    for (let i = 0; i < n; i++) if (!done[i] && (u < 0 || dist[i] < dist[u])) u = i;
    if (u < 0 || dist[u] === Infinity) break;
    done[u] = true;
    if (u === 1) break;
    for (let v = 0; v < n; v++) {
      if (done[v]) continue;
      const d = dist[u] + Math.hypot(nodes[v].x - nodes[u].x, nodes[v].y - nodes[u].y);
      if (d >= dist[v]) continue;
      if (!clear(nodes[u], nodes[v])) continue;
      dist[v] = d;
      prev[v] = u;
    }
  }
  if (dist[1] === Infinity) return null;
  const via: PathPoint[] = [];
  for (let c = prev[1]; c > 0; c = prev[c]) via.unshift(nodes[c]);
  return via;
}
