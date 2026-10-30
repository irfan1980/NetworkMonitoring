import { buildPath, pathMeta, pointAtPath, tangentAtPath, type PathPoint } from './lanePath';

const EM_TEXT = 0.62;

export type DecorKind = 'label' | 'pending' | 'queued' | 'backlog' | 'lost' | 'failedOutbox' | 'failedInbox';

export interface LaneDecorInput {
  kind: DecorKind;
  text: string;
  caption?: string;
}

export interface LaneLayoutInput {
  key: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  scale: number;
  labelIndex: number;
  decors: LaneDecorInput[];
  via?: PathPoint[];
}

export interface DecorPlacement {
  x: number;
  y: number;
  caption: boolean;
}

export type LanePlacement = Partial<Record<DecorKind, DecorPlacement>>;

interface Box {
  left: number;
  right: number;
  top: number;
  bottom: number;
}

const SLOTS: Record<DecorKind, { t: number; side: -1 | 1; mag: number }> = {
  label: { t: 0.12, side: 1, mag: 15 },
  pending: { t: 0.22, side: -1, mag: 24 },
  queued: { t: 0.6, side: -1, mag: 24 },
  backlog: { t: 0.83, side: -1, mag: 24 },
  lost: { t: 0.6, side: 1, mag: 30 },
  failedOutbox: { t: 0.3, side: 1, mag: 26 },
  failedInbox: { t: 0.72, side: 1, mag: 26 },
};

function boxFor(kind: DecorKind, d: LaneDecorInput, x: number, y: number, scale: number, withCaption: boolean): Box {
  if (kind === 'label') {
    const w = d.text.length * 11 * scale * EM_TEXT;
    const h = 13 * scale;
    return { left: x, right: x + w, top: y - h, bottom: y + 3 * scale };
  }
  if (kind === 'lost' || kind === 'failedOutbox' || kind === 'failedInbox') {
    const w = Math.max(d.text.length > 6 ? 60 : 32, d.text.length * 11 * EM_TEXT) * scale;
    const h = 22 * scale;
    return { left: x - w / 2, right: x + w / 2, top: y - h / 2, bottom: y + h / 2 };
  }
  const markerW = Math.max(21, 12 + 8 * d.text.length) * scale;
  const capW = withCaption && d.caption ? d.caption.length * 10 * scale * EM_TEXT : 0;
  const w = Math.max(markerW, capW);
  const top = -9 * scale;
  const bottom = withCaption && d.caption ? 22 * scale : 9 * scale;
  return { left: x - w / 2, right: x + w / 2, top: y + top, bottom: y + bottom };
}

function overlaps(a: Box, b: Box, pad: number): boolean {
  return a.left < b.right + pad && b.left < a.right + pad && a.top < b.bottom + pad && b.top < a.bottom + pad;
}

const CANDIDATES: { dMag: number; dt: number; flip: boolean }[] = [];
for (const dMag of [0, 14, 28, 44, 62, 80]) {
  for (const dt of [0, 0.06, -0.06, 0.12, -0.12, 0.18, -0.18]) {
    CANDIDATES.push({ dMag, dt, flip: false });
  }
}
for (const dMag of [0, 16, 32, 50, 68]) {
  for (const dt of [0, 0.08, -0.08, 0.16, -0.16]) {
    CANDIDATES.push({ dMag, dt, flip: true });
  }
}

export function layoutLaneDecorations(
  lanes: LaneLayoutInput[],
  obstacles: { cx: number; cy: number; r: number }[] = [],
  bounds?: { width: number; height: number }
): Record<string, LanePlacement> {
  const placed: Box[] = [];
  const out: Record<string, LanePlacement> = {};

  for (const o of obstacles) {
    placed.push({ left: o.cx - o.r, right: o.cx + o.r, top: o.cy - o.r, bottom: o.cy + o.r });
  }

  const order: DecorKind[] = ['label', 'pending', 'queued', 'backlog', 'lost', 'failedOutbox', 'failedInbox'];

  for (const kind of order) {
    for (const lane of lanes) {
      const decor = lane.decors.find((d) => d.kind === kind);
      if (!decor) continue;

      const path = buildPath(lane.x1, lane.y1, lane.x2, lane.y2, lane.via);
      const meta = pathMeta(path);
      const len = meta.total;
      const atT = (t: number) => {
        const p = pointAtPath(path, meta.cum, t);
        const tan = tangentAtPath(path, meta.cum, t);
        return { x: p.x, y: p.y, ux: tan.ux, uy: tan.uy };
      };
      const slot = SLOTS[kind];
      const shortLane = len < 320;
      const baseMag = shortLane ? Math.min(slot.mag, 12) : slot.mag + (lane.labelIndex % 3) * 6;
      const maxExtra = shortLane ? 34 : 80;
      const wantCaption = !!decor.caption && !shortLane && len >= 240 && lane.scale >= 0.55;
      const pad = 3;

      let best: { x: number; y: number; caption: boolean } | null = null;
      let fallback: { x: number; y: number; caption: boolean; worst: number } | null = null;

      for (const withCaption of wantCaption ? [true, false] : [false]) {
        for (const c of CANDIDATES) {
          if (c.dMag > maxExtra) continue;
          const t = Math.min(0.95, Math.max(0.05, slot.t + c.dt));
          const side = (c.flip ? -slot.side : slot.side) as -1 | 1;
          const mag = (baseMag + c.dMag) * lane.scale;
          const base = atT(t);
          const ax = base.x - base.uy * mag * side;
          const ay = base.y + base.ux * mag * side;
          const box = boxFor(kind, decor, ax, ay, lane.scale, withCaption);
          if (bounds && (box.left < 2 || box.top < 2 || box.right > bounds.width - 2 || box.bottom > bounds.height - 2)) continue;
          let hit = 0;
          for (const p of placed) {
            if (overlaps(box, p, pad)) hit += 1;
          }
          if (hit === 0) {
            best = { x: ax, y: ay, caption: withCaption };
            placed.push(box);
            break;
          }
          if (!fallback || hit < fallback.worst) fallback = { x: ax, y: ay, caption: withCaption, worst: hit };
        }
        if (best) break;
      }

      const preferredBase = atT(slot.t);
      const preferred = {
        x: preferredBase.x - preferredBase.uy * baseMag * lane.scale * slot.side,
        y: preferredBase.y + preferredBase.ux * baseMag * lane.scale * slot.side,
        caption: false,
      };
      const chosen = best ?? (fallback ? { x: fallback.x, y: fallback.y, caption: false } : preferred);
      if (!best) placed.push(boxFor(kind, decor, chosen.x, chosen.y, lane.scale, chosen.caption));
      out[lane.key] = out[lane.key] ?? {};
      out[lane.key][kind] = chosen;
    }
  }

  return out;
}
