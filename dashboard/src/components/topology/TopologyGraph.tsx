import React, { useEffect, useMemo, useRef, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import Svg, { G, Line, Rect, Text as SvgText } from 'react-native-svg';
import { createThemedStyles } from '../../theme/ThemeContext';
import { colors } from '../../theme/colors';
import { useConnectionStore } from '../../stores/connectionStore';
import { useResponsive } from '../../hooks/useResponsive';
import { useMachineNodesByPlatform, usePlatformList, usePlatformMachineMap } from '../../utils/platformGrouping';
import { receiverBacklogFor as receiverBacklogOf } from '../../utils/pairTotals';
import { queueLevelForCount } from '../../constants/queueThresholds';
import { formatTimeAgo } from '../../utils/format';
import { formatMachineLabel, useMachineLabeler } from '../../utils/machineLabel';
import { pipelineHealthColor } from '../../utils/health';
import type { Pair, PipelineId, StandaloneServiceNode } from '../../types/relay';
import { PairLane } from './PairLane';
import { BundledLane } from './BundledLane';
import { layoutLaneDecorations, type LaneLayoutInput } from './laneLayout';
import { computeBypassPath, defaultBypassMargin, buildPath, segIntersectsRect, shortestClearRoute } from './lanePath';
import { resolveLaneSeparation } from './laneSeparation';
import { PlatformEntity } from './PlatformEntity';
import { useLaneTuning } from '../../hooks/useLaneTuning';
import { GridTopologyView, type GridEdge } from './GridTopologyView';
import { ZoomPanCanvas, type ZoomPanHandle } from './ZoomPanCanvas';
import type { MachineLayoutGroup } from './machineLayout';
import { ExternalInfraStrip } from './ExternalInfraStrip';
import { BusModal } from '../diagnostics/BusModal';
import { PlatformModal } from '../diagnostics/PlatformModal';
import { MachineModal } from '../diagnostics/MachineModal';
import { svgTapProps } from '../../utils/svgPress';
import { dragProps as makeDragProps } from '../../utils/dragGesture';
import {
  canonicalAssignment,
  reorderMachine,
  reorderService,
  idSetHash,
  assignmentMatches,
  type SlotAssignment,
  type MachineGroupInput,
} from './arrangement';
import { loadArrangement, saveArrangement, clearArrangement } from './arrangementStorage';
import { buildGroupsForLayout, resolveMachineLayout, pickCleanRandomAssignment } from './resolveLayout';
import { collectUnorderedEdges } from './laneOverlap';

const useStyles = createThemedStyles((c) => ({
  pickerWrap: { alignItems: 'center', marginTop: 4 },
  picker: { backgroundColor: c.surfaceRaised, borderRadius: 12, padding: 10, gap: 6, borderWidth: 1, borderColor: c.border },
  pickerTitle: { color: c.textMuted, fontSize: 11, fontWeight: '700', marginBottom: 2 },
  pickerRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 6, paddingHorizontal: 10 },
  pickerDot: { width: 8, height: 8, borderRadius: 4 },
  pickerLabel: { color: c.textPrimary, fontSize: 13, fontWeight: '600' },
  pickerClose: { alignSelf: 'center', marginTop: 4 },
  pickerCloseText: { color: c.textMuted, fontSize: 12 },
  arrangeBar: { position: 'absolute', right: 50, bottom: 8, flexDirection: 'row', gap: 6, zIndex: 5 },
  arrangeBtn: {
    backgroundColor: c.surfaceRaised,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: c.border,
    paddingVertical: 6,
    paddingHorizontal: 10,
  },
  arrangeBtnText: { color: c.textPrimary, fontSize: 11, fontWeight: '700' },
  arrangeBtnActive: { backgroundColor: c.good ?? '#2e9e5b', borderColor: c.good ?? '#2e9e5b' },
  arrangeWarning: {
    backgroundColor: c.bad ?? '#e0524f',
    borderRadius: 8,
    paddingVertical: 6,
    paddingHorizontal: 10,
    justifyContent: 'center',
  },
  arrangeWarningText: { color: '#fff', fontSize: 11, fontWeight: '700' },
}));

interface Props {
  width?: number;
  height?: number;
  onSelectPipeline?: (pipelineId: PipelineId) => void;
  onTouchActive?: (active: boolean) => void;
}

const DENSE_THRESHOLD = 40;

export function TopologyGraph({ width = 640, height = 640, onSelectPipeline, onTouchActive }: Props) {
  const styles = useStyles();
  const { isPhone } = useResponsive();
  const topologyNodes = useConnectionStore((s) => s.topologyNodes);
  const pairs = useConnectionStore((s) => s.pairs);
  const pipelines = useConnectionStore((s) => s.pipelines);
  const bus = useConnectionStore((s) => s.bus);
  const buses = useConnectionStore((s) => s.buses);
  const tick = useConnectionStore((s) => s.dataTick) ?? 0;
  const machines = useConnectionStore((s) => s.machines);
  const machineSocketState = useConnectionStore((s) => s.machineSocketState);
  const estateServices = useConnectionStore((s) => s.estateServices);
  const platformMachineMap = usePlatformMachineMap();
  const platformList = usePlatformList();
  const machineLabeler = useMachineLabeler();
  const machineNodesByPlatform = useMachineNodesByPlatform();
  const tuning = useLaneTuning();

  const [pickerFor, setPickerFor] = useState<string | null>(null);
  const [busModalPair, setBusModalPair] = useState<{ from: string; to: string } | null>(null);
  const [platformModalFor, setPlatformModalFor] = useState<string | null>(null);
  const [machineModalFor, setMachineModalFor] = useState<number | null>(null);

  const [assignment, setAssignment] = useState<SlotAssignment | null>(null);
  const [arrangementGen, setArrangementGen] = useState(0);
  const [relocateMode, setRelocateMode] = useState(false);
  const [dragState, setDragState] = useState<
    | { kind: 'machine'; machineId: number; dx: number; dy: number; lastClientX: number; lastClientY: number }
    | { kind: 'service'; machineId: number; platformId: string; dx: number; dy: number; lastClientX: number; lastClientY: number }
    | null
  >(null);
  const scaleRef = useRef(1);
  const zoomRef = useRef<ZoomPanHandle>(null);
  useEffect(() => {
    if (arrangementGen === 0) return;
    zoomRef.current?.resetToFit();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [arrangementGen]);

  const platformIds = useMemo(() => {
    const ids = platformList.map((p) => p.id);
    const keyOf = (id: string) => platformMachineMap[id] ?? -1;
    return [...ids].sort((a, b) => keyOf(a) - keyOf(b));
  }, [platformList, platformMachineMap]);
  const labelOf = useMemo(() => {
    const map: Record<string, string> = {};
    for (const p of platformList) map[p.id] = p.label;
    return map;
  }, [platformList]);
  const typeOf = useMemo(() => {
    const map: Record<string, string> = {};
    for (const p of platformList) map[p.id] = p.type;
    return map;
  }, [platformList]);
  const n = platformIds.length;
  const dense = n > DENSE_THRESHOLD;

  const servicePseudoIds = useMemo(() => estateServices.map((s) => String(s.server_id)), [estateServices]);
  const serviceById = useMemo(() => Object.fromEntries(estateServices.map((s) => [String(s.server_id), s])), [estateServices]);
  // Drops a leading/trailing "-" token (e.g. "MSSQL-", "-Dev") that at least two services share, so "A → B" lane labels stay short; modals keep the full names.
  const shortOf = useMemo(() => {
    const trunc = (n: string) => (n.length > 16 ? `${n.slice(0, 15)}…` : n);
    const parts = estateServices.map((sv) => sv.node_name.split('-'));
    const shared = (pick: (p: string[]) => string) => {
      const count: Record<string, number> = {};
      for (const p of parts) count[pick(p)] = (count[pick(p)] ?? 0) + 1;
      return count;
    };
    const first = shared((p) => p[0]);
    const last = shared((p) => p[p.length - 1]);
    return Object.fromEntries(
      estateServices.map((sv, i) => {
        let p = parts[i];
        if (p.length > 1 && first[p[0]] > 1) p = p.slice(1);
        if (p.length > 1 && last[p[p.length - 1]] > 1) p = p.slice(0, -1);
        return [String(sv.server_id), trunc(p.join('-'))];
      })
    ) as Record<string, string>;
  }, [estateServices]);
  const isService = (id: string) => !dense && serviceById[id] != null;
  const titleOf = (id: string) => serviceById[id]?.node_name ?? machineLabeler.platformTitle(id);

  const shrink = n <= 3 ? 1 : n === 4 ? 0.85 : Math.max(0.5, 3 / n);
  const DESKTOP_MAX_R = 130;
  const r = Math.min(DESKTOP_MAX_R, Math.max(47, Math.min(width, height) * 0.14 * shrink));
  const scale = Math.min(1.15, Math.max(0.4, r / 90));

  // A machine with no platform mapped to it has no nodes; it is drawn in a fixed strip, outside the shuffled arrangement.
  const nodelessMachineIds = useMemo(() => {
    if (dense) return [] as number[];
    const withNodes = new Set<number>();
    for (const id of platformIds) {
      const mid = platformMachineMap[id];
      if (mid != null) withNodes.add(mid);
    }
    return Object.values(machines)
      .map((m) => m.machine_id)
      .filter((id) => !withNodes.has(id))
      .sort((a, b) => a - b);
  }, [dense, platformIds, platformMachineMap, machines]);

  const rawGroups = useMemo<MachineGroupInput[]>(() => {
    if (dense) return [];
    const groups = new Map<number | null, string[]>();
    for (const id of platformIds) {
      const mid = platformMachineMap[id] ?? null;
      const list = groups.get(mid);
      if (list) list.push(id);
      else groups.set(mid, [id]);
    }
    const out: MachineGroupInput[] = Array.from(groups.entries())
      .sort(([a], [b]) => (a == null ? 1 : b == null ? -1 : a - b))
      .map(([machineId, ids]) => ({ machineId, platformIds: ids }));
    return out;
  }, [isPhone, dense, platformIds, platformMachineMap]);

  const edgesForScoring = useMemo(() => collectUnorderedEdges(pairs, new Set(platformIds)), [pairs, platformIds]);

  const arrangementHash = useMemo(() => {
    if (rawGroups.length === 0) return '';
    const ids: string[] = [];
    for (const g of rawGroups) {
      ids.push(`m:${g.machineId}`);
      for (const p of g.platformIds) ids.push(p);
    }
    return idSetHash(ids);
  }, [rawGroups]);

  const lastHashRef = useRef<string>('');
  useEffect(() => {
    if (!arrangementHash || arrangementHash === lastHashRef.current) return;
    lastHashRef.current = arrangementHash;
    const stored = loadArrangement(arrangementHash);
    if (stored && assignmentMatches(stored, rawGroups)) {
      setAssignment(stored);
      return;
    }
    const seed = ((Date.now() ^ Math.floor(Math.random() * 0xffffffff)) >>> 0) || 1;
    const fresh = pickCleanRandomAssignment({ rawGroups, machineRowEligible, r, width, height, edges: edgesForScoring, seed });
    setAssignment(fresh);
    saveArrangement(arrangementHash, fresh);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [arrangementHash]);

  const machineGroupsForLayout = useMemo<MachineLayoutGroup[]>(() => {
    if (dense || !assignment) return [];
    return buildGroupsForLayout(rawGroups, assignment);
  }, [dense, assignment, rawGroups]);

  const machineRowEligible = useMemo(() => {
    const eligible = new Map<number, boolean>();
    if (dense) return eligible;
    const platformsByMachine = new Map<number, string[]>();
    for (const id of platformIds) {
      const mid = platformMachineMap[id];
      if (mid == null) continue;
      const list = platformsByMachine.get(mid);
      if (list) list.push(id);
      else platformsByMachine.set(mid, [id]);
    }
    for (const [mid, ids] of platformsByMachine) {
      const targetCounts = new Map<number, number>();
      for (const id of ids) {
        const partnerMachines = new Set<number>();
        for (const other of platformIds) {
          if (other === id) continue;
          const otherMid = platformMachineMap[other];
          if (otherMid == null || otherMid === mid) continue;
          if (pairs[`${id}->${other}`] || pairs[`${other}->${id}`]) partnerMachines.add(otherMid);
        }
        for (const pm of partnerMachines) targetCounts.set(pm, (targetCounts.get(pm) ?? 0) + 1);
      }
      const risky = Array.from(targetCounts.values()).some((count) => count >= 2);
      eligible.set(mid, !risky);
    }
    return eligible;
  }, [isPhone, dense, platformIds, platformMachineMap, pairs]);

  const resolvedLayout = useMemo(() => {
    if (machineGroupsForLayout.length === 0 || !assignment) return null;
    return resolveMachineLayout({
      groups: machineGroupsForLayout,
      mode: assignment.mode,
      rowWidthMult: assignment.rowWidthMult,
      gapTierOffset: assignment.gapTierOffset,
      machineRowEligible,
      r,
      width,
      height,
      edges: edgesForScoring,
    });
  }, [machineGroupsForLayout, machineRowEligible, r, width, height, assignment, edgesForScoring]);
  const machineLayout = resolvedLayout?.layout ?? null;

  // Independent services and node-less machines live in fixed left strips that shuffle/relocate never touch; the machine layout is offset to their right.
  const mainHeight = Math.max(height, machineLayout?.worldHeight ?? 0);
  const { strip, nodelessStrip } = useMemo(() => {
    const nSvc = dense ? 0 : estateServices.length;
    const nNl = nodelessMachineIds.length;
    const svcPairs = new Set<string>();
    for (const p of Object.values(pairs)) {
      if (serviceById[p.from] && serviceById[p.to]) svcPairs.add([p.from, p.to].sort().join('|'));
    }
    const svcDims = (k: number) => {
      const cardW = Math.max(260, r * 2.2) * k;
      const cardH = 80 * k;
      const nodeMinK = tuning.nodeMin * k;
      const slot = Math.max(cardH + 110 * k, tuning.busMin * k + 30 * k);
      const textW = (t: string) => t.length * 11 * 0.62 * Math.max(scale, 0.9) * k * 1.1;
      // Each service pair gets two adjacent channels with their labels beside them: inner label left of its channel, outer label right of its own.
      const pairX: Record<string, { gx0: number; gx1: number; w: number }> = {};
      let x = 0;
      for (const key of svcPairs) {
        const [pa, pb] = key.split('|');
        const w = Math.max(textW(`${shortOf[pa]} → ${shortOf[pb]}`), textW(`${shortOf[pb]} → ${shortOf[pa]}`));
        const inner = Math.max(nodeMinK, w + 14 * k);
        pairX[key] = { gx0: x + inner, gx1: x + inner + 20 * k, w };
        x += inner + 20 * k + w + 12 * k;
      }
      const gutter = svcPairs.size > 0 ? x + 8 * k : 0;
      return { k, cardW, cardH, slot, pad: 24 * k, header: 64 * k, gutter, pairX, w: 24 * k + cardW + gutter + 24 * k, h: 64 * k + nSvc * slot - (slot - cardH) / 2 };
    };
    const nlDims = (k: number) => {
      const cardW = 310 * k;
      const cardH = 108 * k;
      return { k, cardW, cardH, slot: cardH + 18 * k, pad: 24 * k, header: 64 * k, w: cardW + 48 * k, h: 64 * k + nNl * (cardH + 18 * k) + 6 * k };
    };
    // The fit shrinks the whole map to the viewport; grow the strips (within the main layout's height) so their text stays readable at fit zoom.
    const fitEst = Math.min(1, height / Math.max(1, mainHeight));
    const tallest = Math.max(nSvc ? svcDims(1).h : 0, nNl ? nlDims(1).h : 0);
    const k = tallest > 0 ? Math.max(1, Math.min(0.85 / fitEst, 2.4, mainHeight / tallest)) : 1;
    const svc = nSvc ? svcDims(k) : null;
    const nl = nNl ? nlDims(k) : null;
    const colGap = 20 * k;
    const sideGap = 56 * k;
    // Below the services when it fits the main layout's height (one shared column width), else a second column to the right.
    const stacked = !!svc && !!nl && svc.h + colGap + nl.h <= mainHeight;
    const colW = Math.max(svc?.w ?? 0, nl?.w ?? 0);
    const svcW = stacked ? colW : svc?.w ?? 0;
    const strip = svc
      ? {
          x: 0,
          y: 0,
          w: svcW,
          h: svc.h,
          k,
          exitX: svcW + (stacked || !nl ? 24 * k : sideGap / 2),
          cardW: svc.cardW,
          cardH: svc.cardH,
          header: svc.header,
          arc: { right: svc.pad + svc.cardW, pairX: svc.pairX },
          positions: Object.fromEntries(
            servicePseudoIds.map((pid, i) => [pid, { x: svc.pad + svc.cardW / 2, y: svc.header + i * svc.slot + svc.cardH / 2 }])
          ) as Record<string, { x: number; y: number }>,
        }
      : null;
    const nodelessStrip = nl
      ? {
          x: stacked || !svc ? 0 : svc.w + sideGap,
          y: stacked && svc ? svc.h + colGap : 0,
          w: stacked ? colW : nl.w,
          h: nl.h,
          k,
          cardW: nl.cardW,
          cardH: nl.cardH,
          pad: stacked ? (colW - nl.cardW) / 2 : nl.pad,
          header: nl.header,
          slot: nl.slot,
        }
      : null;
    return { strip, nodelessStrip };
  }, [dense, estateServices.length, servicePseudoIds, serviceById, shortOf, pairs, r, scale, tuning.nodeMin, tuning.busMin, nodelessMachineIds.length, mainHeight, height]);

  const K = strip?.k ?? nodelessStrip?.k ?? 1;

  // Section borders are blue, red only once every member is confirmed down/offline; pending/unknown stays blue.
  const servicesBorder = estateServices.length > 0 && estateServices.every((sv) => sv.status === 'down' || sv.status === 'offline') ? colors.bad : colors.accent;
  const nodelessBorder = nodelessMachineIds.length > 0 && nodelessMachineIds.every((id) => machines[id]?.status === 'offline') ? colors.bad : colors.accent;

  const stripOffsetX = Math.max(strip ? strip.x + strip.w : 0, nodelessStrip ? nodelessStrip.x + nodelessStrip.w : 0) + (strip || nodelessStrip ? Math.max(24, r * 0.35) * 2 : 0);
  const stripsHeight = Math.max(strip ? strip.y + strip.h : 0, nodelessStrip ? nodelessStrip.y + nodelessStrip.h : 0);

  const worldHeight = Math.max(height, machineLayout?.worldHeight ?? 0, stripsHeight);
  // Fit is height-limited when the world is taller than the viewport, so widen the world to the viewport's aspect to fill the width instead of leaving side bars.
  const worldWidth = Math.max(width * (worldHeight / height), machineLayout ? machineLayout.worldWidth + stripOffsetX : 0);

  const positions = useMemo(() => {
    const result: Record<string, { x: number; y: number }> = {};
    if (n === 0) return result;
    if (!dense && machineLayout) {
      const offsetX = stripOffsetX + Math.max(0, (worldWidth - stripOffsetX - machineLayout.worldWidth) / 2);
      const offsetY = Math.max(0, (worldHeight - machineLayout.worldHeight) / 2);
      for (const [id, p] of Object.entries(machineLayout.positions)) {
        result[id] = { x: p.x + offsetX, y: p.y + offsetY };
      }
      return result;
    }
    {
      const cx = width / 2;
      const cy = height / 2;
      const rx = Math.max(40, width / 2 - r - 30);
      const ry = Math.max(40, height / 2 - r - 30);
      const baseAngle = (7 * Math.PI) / 6;
      platformIds.forEach((id, i) => {
        const theta = baseAngle + (i * 2 * Math.PI) / n;
        result[id] = { x: cx + rx * Math.cos(theta), y: cy + ry * Math.sin(theta) };
      });
    }
    return result;
  }, [isPhone, dense, width, height, r, n, platformIds, machineLayout, worldWidth, worldHeight, platformMachineMap, stripOffsetX]);

  const machineBoxes = useMemo(() => {
    type Box = { machineId: number; x: number; y: number; w: number; h: number; placeholder: boolean };
    if (dense) return [] as Box[];
    if (!machineLayout) return [] as Box[];
    const offsetX = stripOffsetX + Math.max(0, (worldWidth - stripOffsetX - machineLayout.worldWidth) / 2);
    const offsetY = Math.max(0, (worldHeight - machineLayout.worldHeight) / 2);
    return machineLayout.boxes
      .filter((b): b is typeof b & { machineId: number } => b.machineId != null)
      .map((b) => ({ machineId: b.machineId, x: b.x + offsetX, y: b.y + offsetY, w: b.w, h: b.h, placeholder: b.placeholder }));
  }, [dense, machineLayout, worldWidth, worldHeight, stripOffsetX]);

  // Content can extend past the layout's nominal world (restored or relocated boxes), so fit and clip to what is drawn.
  const canvasHeight = useMemo(() => {
    const boxBottom = machineBoxes.reduce((m, b) => Math.max(m, b.y + b.h), 0);
    const nodeBottom = Object.values(positions).reduce((m, p) => Math.max(m, p.y + r * 1.6), 0);
    return Math.max(worldHeight, boxBottom + 12, nodeBottom, stripsHeight + 12);
  }, [machineBoxes, positions, r, worldHeight, stripsHeight]);

  const canvasWidth = useMemo(() => {
    const boxRight = machineBoxes.reduce((m, b) => Math.max(m, b.x + b.w), 0);
    const nodeRight = Object.values(positions).reduce((m, p) => Math.max(m, p.x + r * 1.6), 0);
    return Math.max(worldWidth, boxRight + 12, nodeRight, (width * canvasHeight) / height);
  }, [machineBoxes, positions, r, worldWidth, width, height, canvasHeight]);

  const lanePos = useMemo(() => ({ ...positions, ...(strip?.positions ?? {}) }), [positions, strip]);

  // Strips act as obstacles for lanes that do not start or end in them (-1 services, -2 node-less machines).
  const obstacleBoxes = useMemo(() => {
    const out = machineBoxes.map((b) => ({ machineId: b.machineId as number, x: b.x, y: b.y, w: b.w, h: b.h }));
    if (strip) out.push({ machineId: -1, x: strip.x, y: strip.y, w: strip.w, h: strip.h });
    if (nodelessStrip) out.push({ machineId: -2, x: nodelessStrip.x, y: nodelessStrip.y, w: nodelessStrip.w, h: nodelessStrip.h });
    return out;
  }, [machineBoxes, strip, nodelessStrip]);
  const boxIdOf = (id: string): number | undefined => platformMachineMap[id] ?? (isService(id) ? -1 : undefined);

  function machineBoxLabel(machineId: number, maxChars?: number): { title: string; detail: string; online: boolean; state: 'online' | 'pending' | 'offline' } {
    const info = machines[machineId];
    const socketState = machineSocketState[machineId];
    if (!info) return { title: `Machine ${machineId}`, detail: 'unknown', online: false, state: 'pending' };
    if (info.status === 'online') {
      const pingText = info.ping?.rtt_ms != null ? `${Math.round(info.ping.rtt_ms)}ms` : null;
      const detail =
        socketState === 'live'
          ? `online${pingText ? ` · ping ${pingText}` : ''}`
          : `online · connecting…`;
      return { title: formatMachineLabel(info.machine_name, info.machine_label_id ?? machineId, maxChars), detail, online: true, state: 'online' };
    }
    if (info.status === 'pending') {
      return { title: formatMachineLabel(info.machine_name, info.machine_label_id ?? machineId, maxChars), detail: 'registered, never seen online', online: false, state: 'pending' };
    }
    const lastActive = info.last_active_ts ? `last active ${formatTimeAgo(info.last_active_ts)}` : 'never seen online';
    return { title: formatMachineLabel(info.machine_name, info.machine_label_id ?? machineId, maxChars), detail: `offline · ${lastActive}`, online: false, state: 'offline' };
  }

  function entitySubLabel(platformId: string): string | undefined {
    if (ownerMachineOnline(platformId)) return undefined;
    const node = machineNodesByPlatform[platformId];
    if (!node) return undefined;
    if (node.last_known_status === 'unknown' || !node.last_known_ts) return 'never observed';
    return `last known ${node.last_known_status} · ${formatTimeAgo(node.last_known_ts)}`;
  }

  // Only trust a ring's colour while its machine is online, or a stale last-known ring would read green.
  function ownerMachineOnline(platformId: string): boolean {
    const machineId = platformMachineMap[platformId];
    return machineId != null && machines[machineId]?.status === 'online';
  }

  function entityState(platformId: string): 'ok' | 'grey' | 'down' | 'yellow' | 'degraded' | 'red' {
    if (!ownerMachineOnline(platformId)) return 'grey';
    const node = topologyNodes[platformId];
    if (!node) return 'grey';
    if (node.dbOk === false) return 'down';
    if (node.senderOk && node.receiverOk) return 'ok';
    const worstPending = Math.max(
      node.senderOk ? -1 : node.senderPendingCount ?? -1,
      node.receiverOk ? -1 : node.receiverPendingCount ?? -1
    );
    const level = queueLevelForCount(worstPending < 0 ? null : worstPending);
    if (level === 'red') return 'red';
    if (level === 'degraded') return 'degraded';
    if (level === 'yellow') return 'yellow';
    return 'grey';
  }

  function pairBusDown(pair: Pair | undefined): boolean {
    if (!pair) return false;
    if (pair.bus) {
      const entry = buses[pair.bus];
      if (entry) return entry.ok === false;
    }
    return bus?.ok === false || pair.blockedBy === 'service_bus';
  }

  function segmentGrey(pair: Pair | undefined, fromPlatform: string, toPlatform: string) {
    const fromNode = topologyNodes[fromPlatform];
    const toNode = topologyNodes[toPlatform];
    const senderGrey = !!pair && (pair.senderOk === false || fromNode?.dbOk === false);
    const busGrey = pairBusDown(pair);
    const receiverGrey = !!pair && (pair.receiverOk === false || toNode?.dbOk === false);
    const senderRelayDown = !!pair && pair.senderOk === false && fromNode?.dbOk !== false;
    const receiverRelayDown = !!pair && pair.receiverOk === false && toNode?.dbOk !== false;
    const senderPendingCount = senderRelayDown ? topologyNodes[`relay:${fromPlatform}:sender`]?.pendingCount ?? null : null;
    const receiverPendingCount = receiverRelayDown ? topologyNodes[`relay:${toPlatform}:receiver`]?.pendingCount ?? null : null;
    return { senderGrey, busGrey, receiverGrey, senderPendingCount, receiverPendingCount };
  }

  function bundledGrey(
    forward: Pair | undefined,
    backward: Pair | undefined,
    fwdGrey: { senderGrey: boolean; busGrey: boolean; receiverGrey: boolean },
    bwdGrey: { senderGrey: boolean; busGrey: boolean; receiverGrey: boolean }
  ): boolean {
    const fwdBroken = !!forward && (fwdGrey.senderGrey || fwdGrey.busGrey || fwdGrey.receiverGrey);
    const bwdBroken = !!backward && (bwdGrey.senderGrey || bwdGrey.busGrey || bwdGrey.receiverGrey);
    const dirs = [forward ? fwdBroken : null, backward ? bwdBroken : null].filter((v): v is boolean => v !== null);
    return dirs.length > 0 && dirs.every(Boolean);
  }

  function receiverBacklogFor(toPlatform: string): number {
    return receiverBacklogOf(toPlatform, topologyNodes, pipelines);
  }

  // Distance from a node's centre to its edge along (ux, uy): the ring radius, or the card's rectangle for a service.
  function reach(id: string, ux: number, uy: number) {
    const sv = isService(id) ? strip : null;
    if (!sv) return r;
    const tx = Math.abs(ux) < 1e-6 ? Infinity : sv.cardW / 2 / Math.abs(ux);
    const ty = Math.abs(uy) < 1e-6 ? Infinity : sv.cardH / 2 / Math.abs(uy);
    return Math.min(tx, ty);
  }

  function laneEndpoints(a: string, b: string, offset: number) {
    const pa = lanePos[a];
    const pb = lanePos[b];
    const dx = pb.x - pa.x;
    const dy = pb.y - pa.y;
    const len = Math.max(1, Math.hypot(dx, dy));
    const ux = dx / len;
    const uy = dy / len;
    const px = -uy;
    const py = ux;
    const ra = reach(a, ux, uy);
    const rb = reach(b, ux, uy);
    const start = isService(a) ? { x: pa.x + (strip?.cardW ?? 0) / 2, y: pa.y + offset } : { x: pa.x + ux * ra + px * offset, y: pa.y + uy * ra + py * offset };
    const end = isService(b) ? { x: pb.x + (strip?.cardW ?? 0) / 2, y: pb.y + offset } : { x: pb.x - ux * rb + px * offset, y: pb.y - uy * rb + py * offset };
    return { x1: start.x, y1: start.y, x2: end.x, y2: end.y };
  }

  function anchorFacing(id: string, center: { x: number; y: number }, toward: { x: number; y: number }, px: number, py: number, offset: number) {
    const dx = toward.x - center.x;
    const dy = toward.y - center.y;
    const len = Math.max(1, Math.hypot(dx, dy));
    const ux = dx / len;
    const uy = dy / len;
    if (isService(id)) return { x: center.x + (strip?.cardW ?? 0) / 2, y: center.y + offset };
    const rr = reach(id, ux, uy);
    return { x: center.x + ux * rr + px * offset, y: center.y + uy * rr + py * offset };
  }

  const laneOffset = Math.max(12, r * 0.18);

  const BYPASS_MARGIN = Math.max(24, r * 0.35);
  function otherBoxesFor(a: string, b: string) {
    if (dense) return [];
    const midA = boxIdOf(a);
    const midB = boxIdOf(b);
    const boxes =
      obstacleBoxes.length === 0
        ? []
        : obstacleBoxes
            .filter((box) => box.machineId !== midA && box.machineId !== midB)
            .map((box) => ({ x: box.x, y: box.y, w: box.w, h: box.h }));
    const pa = lanePos[a];
    const pb = lanePos[b];
    if (pa && pb && (midA != null || midB != null)) {
      const dx = pb.x - pa.x;
      const dy = pb.y - pa.y;
      const lenSq = dx * dx + dy * dy;
      const candidates = new Set<string>();
      for (const id of platformIds) {
        if (id === a || id === b) continue;
        const mid = platformMachineMap[id];
        if (mid == null || (mid !== midA && mid !== midB)) continue;
        candidates.add(id);
      }
      for (const id of candidates) {
        const p = positions[id];
        if (!p || lenSq < 1e-6) continue;
        const t = ((p.x - pa.x) * dx + (p.y - pa.y) * dy) / lenSq;
        if (t <= 0.02 || t >= 0.98) continue;
        const projX = pa.x + t * dx;
        const projY = pa.y + t * dy;
        const perpDist = Math.hypot(p.x - projX, p.y - projY);
        if (perpDist < r * 1.5) boxes.push({ x: p.x - r, y: p.y - r, w: r * 2, h: r * 2 });
      }
    }
    return boxes;
  }
  function laneStagger(a: string, b: string): number {
    const key = `${a}->${b}`;
    let h = 0;
    for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
    const bucket = h % 11;
    const unit = Math.max(18, r * 0.9);
    return bucket * unit;
  }

  function sameMachineBoundsFor(a: string, b: string) {
    const midA = boxIdOf(a);
    const midB = boxIdOf(b);
    if (midA == null || midA !== midB) return null;
    const box = obstacleBoxes.find((b2) => b2.machineId === midA);
    return box ? { x: box.x, y: box.y, w: box.w, h: box.h } : null;
  }

  // Service-to-service lanes leave the cards' right edges, run down their own channel and enter the target's right edge; channel and stubs honour the lane minimums.
  function serviceArc(a: string, b: string) {
    const pa = lanePos[a];
    const pb = lanePos[b];
    const k = strip?.k ?? 1;
    const arc = strip?.arc;
    const right = arc?.right ?? pa.x;
    const d = a < b ? 0 : 1;
    const px = arc?.pairX[[a, b].sort().join('|')];
    const gx = right + (d ? px?.gx1 : px?.gx0 ?? 0)!;
    const stub = gx - right;
    const sy = (20 + d * 14) * k;
    const ya = pa.y + sy;
    const yb = pb.y + sy;
    const len = 2 * stub + Math.abs(yb - ya);
    const w = px?.w ?? 0;
    const labelW = `${shortOf[a]} → ${shortOf[b]}`.length * 11 * 0.62 * Math.max(scale, 0.9) * k;
    return {
      x1: right,
      y1: ya,
      x2: right,
      y2: yb,
      via: [{ x: gx, y: ya }, { x: gx, y: yb }],
      splits: [stub / len, 1 - stub / len] as [number, number],
      gx,
      d,
      k,
      w,
      labelX: d ? gx + 8 * k : gx - 8 * k - labelW,
      labelY: (ya + yb) / 2,
    };
  }
  const serviceArcRoute = (a: string, b: string) => {
    const { x1, y1, x2, y2, via, splits } = serviceArc(a, b);
    return { x1, y1, x2, y2, via, splits };
  };

  const laneScale = (a: string, b: string) => (isService(a) && isService(b) ? Math.max(scale, 0.9) * K : isService(a) || isService(b) ? scale * Math.min(K, 1.8) : scale);

  function laneRoute(a: string, b: string, offset: number) {
    if (isService(a) && isService(b)) return serviceArcRoute(a, b);
    const ends = laneEndpoints(a, b, offset);
    // Lanes to or from a service card first run straight out of the strip, so the routing below starts outside it.
    const border = strip?.exitX ?? 0;
    // With the node-less section beside the services, drop through the gap below both and route from there, never over the top edge.
    const dropY = strip && nodelessStrip && nodelessStrip.x > strip.x + strip.w ? Math.max(strip.h, nodelessStrip.h) + BYPASS_MARGIN : null;
    const s0 = { x: isService(a) ? border : ends.x1, y: isService(a) && dropY != null ? dropY : ends.y1 };
    const e0 = { x: isService(b) ? border : ends.x2, y: isService(b) && dropY != null ? dropY : ends.y2 };
    const outOfStrip = (route: { x: number; y: number }[]) => [
      ...(isService(a) ? [{ x: border, y: ends.y1 }, ...(dropY != null ? [{ x: border, y: dropY }] : [])] : []),
      ...route,
      ...(isService(b) ? [...(dropY != null ? [{ x: border, y: dropY }] : []), { x: border, y: ends.y2 }] : []),
    ];
    const via = computeBypassPath(
      s0,
      e0,
      otherBoxesFor(a, b),
      BYPASS_MARGIN,
      laneStagger(a, b),
      sameMachineBoundsFor(a, b)
    );
    if (!via) return { ...ends, via: isService(a) || isService(b) ? outOfStrip([]) : undefined };
    const pa = lanePos[a];
    const pb = lanePos[b];
    const dx = pb.x - pa.x;
    const dy = pb.y - pa.y;
    const len = Math.max(1, Math.hypot(dx, dy));
    const px = -dy / len;
    const py = dx / len;
    const anchor = (v: { x: number; y: number }[]) => ({
      p1: anchorFacing(a, pa, v[0], px, py, offset),
      p2: anchorFacing(b, pb, v[v.length - 1], px, py, offset),
    });
    const midA = boxIdOf(a);
    const midB = boxIdOf(b);
    const foreign = obstacleBoxes.filter((bx) => bx.machineId !== midA && bx.machineId !== midB);
    const clearOf = (pts: { x: number; y: number }[]) =>
      foreign.every((bx) => pts.every((p, i) => i === 0 || !segIntersectsRect(pts[i - 1], p, bx, BYPASS_MARGIN * 0.4)));
    const nudgeInfl = Math.abs(offset + laneOffset);
    let route = via;
    let { p1, p2 } = anchor(route);
    if (foreign.length > 0 && (nudgeInfl > 0 || !clearOf([p1, ...route, p2]))) {
      for (const extra of [0, BYPASS_MARGIN, BYPASS_MARGIN * 2]) {
        const alt = shortestClearRoute(s0, e0, foreign, BYPASS_MARGIN, extra + nudgeInfl);
        if (!alt || alt.length === 0) continue;
        const anchored = anchor(alt);
        route = alt;
        p1 = anchored.p1;
        p2 = anchored.p2;
        if (clearOf([p1, ...route, p2])) break;
      }
    }
    return { x1: p1.x, y1: p1.y, x2: p2.x, y2: p2.y, via: outOfStrip(route) };
  }

  const pickerNode = pickerFor ? topologyNodes[pickerFor] : null;
  const pickerPipelines = pickerNode?.pipelines?.map((id) => pipelines[id]).filter(Boolean) ?? [];

  const pairKeys = useMemo(() => {
    if (dense) return [];
    const out: [string, string][] = [];
    const ids = [...platformIds, ...servicePseudoIds];
    for (let i = 0; i < ids.length; i++) {
      for (let j = i + 1; j < ids.length; j++) out.push([ids[i], ids[j]]);
    }
    return out;
  }, [dense, platformIds, servicePseudoIds]);



  const SEPARATION_UNIT = Math.max(14, r * 0.5);
  const laneSeparation = useMemo(() => {
    if (dense) return { extra: {} as Record<string, number>, remaining: 0 };
    const laneIds: string[] = [];
    for (const [a, b] of pairKeys) {
      if (pairs[`${a}->${b}`]) laneIds.push(`${a}->${b}`);
      if (pairs[`${b}->${a}`]) laneIds.push(`${b}->${a}`);
    }
    if (laneIds.length === 0) return { extra: {} as Record<string, number>, remaining: 0 };
    const buildRoute = (id: string, extraOffset: number) => {
      const arrow = id.indexOf('->');
      const from = id.slice(0, arrow);
      const to = id.slice(arrow + 2);
      const route = laneRoute(from, to, -laneOffset + extraOffset);
      return buildPath(route.x1, route.y1, route.x2, route.y2, route.via);
    };
    const isClear = (id: string, pts: { x: number; y: number }[]) => {
      const arrow = id.indexOf('->');
      const midA = boxIdOf(id.slice(0, arrow));
      const midB = boxIdOf(id.slice(arrow + 2));
      for (const box of obstacleBoxes) {
        if (box.machineId === midA || box.machineId === midB) continue;
        for (let i = 0; i < pts.length - 1; i++) if (segIntersectsRect(pts[i], pts[i + 1], box, -2)) return false;
      }
      return true;
    };
    return resolveLaneSeparation(laneIds, buildRoute, SEPARATION_UNIT, isClear);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dense, pairKeys, pairs, lanePos, obstacleBoxes, laneOffset, SEPARATION_UNIT]);
  const laneExtraOffset = laneSeparation.extra;
  const layoutOverlapCount = laneSeparation.remaining;

  const gridEdges = useMemo<GridEdge[]>(() => {
    if (!dense) return [];
    return Object.values(pairs).map((p) => ({ key: p.id, from: p.from, to: p.to, status: p.status }));
  }, [dense, pairs]);

  const placements = useMemo(() => {
    if (dense) return {};
    const inputs: LaneLayoutInput[] = [];
    const addLane = (
      key: string,
      pair: Pair,
      ends: { x1: number; y1: number; x2: number; y2: number; via?: { x: number; y: number }[] },
      fromLabel: string,
      labelIndex: number,
      backlog: number,
      laneSc: number
    ) => {
      const decors: LaneLayoutInput['decors'] = [{ kind: 'label', text: `${fromLabel} →` }];
      if (pair.pendingOutbox > 0) decors.push({ kind: 'pending', text: String(pair.pendingOutbox), caption: 'to send' });
      if (pair.queuedUnknown === true || pair.queued == null) decors.push({ kind: 'queued', text: '?', caption: 'checking…' });
      else if ((pair.queued ?? 0) > 0) decors.push({ kind: 'queued', text: String(pair.queued), caption: 'queued' });
      if (backlog > 0) decors.push({ kind: 'backlog', text: String(backlog), caption: 'to apply' });
      if (pair.lost > 0) decors.push({ kind: 'lost', text: `lost ${pair.lost}` });
      const laneShort = Math.hypot(ends.x2 - ends.x1, ends.y2 - ends.y1) < 320;
      const failedText = (n: number) => (laneShort ? `✕ ${n}` : `✕ ${n} failed`);
      if ((pair.failedOutbox ?? 0) > 0) decors.push({ kind: 'failedOutbox', text: failedText(pair.failedOutbox ?? 0) });
      if ((pair.failedInbox ?? 0) > 0) decors.push({ kind: 'failedInbox', text: failedText(pair.failedInbox ?? 0) });
      const { x1, y1, x2, y2, via } = ends;
      inputs.push({ key, x1, y1, x2, y2, scale: laneSc, labelIndex, decors, via });
    };

    pairKeys.forEach(([a, b], pairKeyIndex) => {
      const forward = pairs[`${a}->${b}`] as Pair | undefined;
      const backward = pairs[`${b}->${a}`] as Pair | undefined;
      if (forward)
        addLane(
          `${a}-${b}`,
          forward,
          laneRoute(a, b, -laneOffset + (laneExtraOffset[`${a}->${b}`] ?? 0)),
          serviceById[a]?.node_name ?? labelOf[a] ?? a,
          pairKeyIndex * 2,
          receiverBacklogFor(b),
          laneScale(a, b)
        );
      if (backward)
        addLane(
          `${b}-${a}`,
          backward,
          laneRoute(b, a, -laneOffset + (laneExtraOffset[`${b}->${a}`] ?? 0)),
          serviceById[b]?.node_name ?? labelOf[b] ?? b,
          pairKeyIndex * 2 + 1,
          receiverBacklogFor(a),
          laneScale(a, b)
        );
    });

    const rings = platformIds.map((id) => ({ cx: positions[id].x, cy: positions[id].y, r }));
    // Keep lane labels and badges off the left sections: cover each with a grid of small squares.
    for (const box of [strip, nodelessStrip]) {
      if (!box) continue;
      for (let gx = box.x + 20; gx < box.x + box.w; gx += 40) {
        for (let gy = box.y + 20; gy < box.y + box.h; gy += 40) rings.push({ cx: gx, cy: gy, r: 20 });
      }
    }
    const out = layoutLaneDecorations(inputs, rings, { width, height });
    if (strip?.arc) {
      for (const [a, b] of pairKeys) {
        if (!isService(a) || !isService(b)) continue;
        for (const [from, to] of [[a, b], [b, a]]) {
          const lane = out[`${from}-${to}`];
          if (!lane) continue;
          const arc = serviceArc(from, to);
          let n = 0;
          for (const kind of Object.keys(lane) as (keyof typeof lane)[]) {
            lane[kind] = kind === 'label'
              ? { x: arc.labelX, y: arc.labelY - 8 * arc.k, caption: false }
              : { x: arc.gx + (arc.d ? 1 : -1) * 16 * arc.k, y: arc.labelY + 26 * arc.k * ++n, caption: false };
          }
        }
      }
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dense, pairs, pipelines, positions, platformIds, r, scale, laneOffset, laneExtraOffset, labelOf, pairKeys, topologyNodes, width, height, machineBoxes, obstacleBoxes, lanePos, platformMachineMap, strip, nodelessStrip]);

  const renderLanes = (layer: 'lines' | 'labels') => (
    <G>
      {dense
          ? (layer === 'labels' ? null : pairKeys.map(([a, b]) => {
              const forward = pairs[`${a}->${b}`] as Pair | undefined;
              const backward = pairs[`${b}->${a}`] as Pair | undefined;
              const fwdGrey = segmentGrey(forward, a, b);
              const bwdGrey = segmentGrey(backward, b, a);
              const bothDown = bundledGrey(forward, backward, fwdGrey, bwdGrey);
              const hasIssues = !!(forward?.issues?.length || backward?.issues?.length);
              return (
                <BundledLane
                  key={`${a}-${b}`}
                  {...laneEndpoints(a, b, 0)}
                  forward={forward}
                  backward={backward}
                  bothDown={bothDown}
                  fwdGrey={fwdGrey}
                  bwdGrey={bwdGrey}
                  hasIssues={hasIssues}
                  scale={scale}
                  onPress={() => setBusModalPair(forward ? { from: a, to: b } : { from: b, to: a })}
                />
              );
            }))
          : pairKeys.map(([a, b], pairKeyIndex) => {
              const forward = pairs[`${a}->${b}`] as Pair | undefined;
              const backward = pairs[`${b}->${a}`] as Pair | undefined;
              const fwdGrey = segmentGrey(forward, a, b);
              const bwdGrey = segmentGrey(backward, b, a);
              const fwdRoute = forward ? laneRoute(a, b, -laneOffset + (laneExtraOffset[`${a}->${b}`] ?? 0)) : null;
              const bwdRoute = backward ? laneRoute(b, a, -laneOffset + (laneExtraOffset[`${b}->${a}`] ?? 0)) : null;
              return (
                <React.Fragment key={`${a}-${b}`}>
                  {forward && fwdRoute ? (
                    <PairLane
                      x1={fwdRoute.x1}
                      y1={fwdRoute.y1}
                      x2={fwdRoute.x2}
                      y2={fwdRoute.y2}
                      viaPoints={fwdRoute.via}
                      pair={forward}
                      fromLabel={titleOf(a)}
                      toLabel={titleOf(b)}
                      scale={laneScale(a, b)}
                      labelIndex={pairKeyIndex * 2}
                      {...fwdGrey}
                      receiverBacklog={receiverBacklogFor(b)}
                      onPressQueue={() => setPickerFor(b)}
                      onPressReceiverBacklog={() => setPickerFor(b)}
                      onPress={() => setBusModalPair({ from: a, to: b })}
                      tick={tick}
                      missingEnd={forward.missingEnd}
                      missingEndReason={forward.missingEndReason}
                      layer={layer}
                      placement={placements[`${a}-${b}`]}
                      nodeMin={tuning.nodeMin}
                      busMin={tuning.busMin}
                      splits={(fwdRoute as { splits?: [number, number] }).splits}
                      endArrow={isService(a) || isService(b)}
                      labelText={isService(a) && isService(b) ? `${shortOf[a]} → ${shortOf[b]}` : undefined}
                    />
                  ) : null}
                  {backward && bwdRoute ? (
                    <PairLane
                      x1={bwdRoute.x1}
                      y1={bwdRoute.y1}
                      x2={bwdRoute.x2}
                      y2={bwdRoute.y2}
                      viaPoints={bwdRoute.via}
                      pair={backward}
                      fromLabel={titleOf(b)}
                      toLabel={titleOf(a)}
                      scale={laneScale(a, b)}
                      labelIndex={pairKeyIndex * 2 + 1}
                      {...bwdGrey}
                      receiverBacklog={receiverBacklogFor(a)}
                      onPressQueue={() => setPickerFor(a)}
                      onPressReceiverBacklog={() => setPickerFor(a)}
                      onPress={() => setBusModalPair({ from: b, to: a })}
                      tick={tick}
                      missingEnd={backward.missingEnd}
                      missingEndReason={backward.missingEndReason}
                      layer={layer}
                      placement={placements[`${b}-${a}`]}
                      nodeMin={tuning.nodeMin}
                      busMin={tuning.busMin}
                      splits={(bwdRoute as { splits?: [number, number] }).splits}
                      endArrow={isService(a) || isService(b)}
                      labelText={isService(a) && isService(b) ? `${shortOf[b]} → ${shortOf[a]}` : undefined}
                    />
                  ) : null}
                </React.Fragment>
              );
            })}
    </G>
  );

  if (dense) {
    return (
      <View style={{ width, alignSelf: 'center' }}>
        <GridTopologyView
          width={width}
          height={height}
          platformIds={platformIds}
          labelOf={labelOf}
          typeOf={typeOf}
          entityState={entityState}
          hasIssues={(id) => (topologyNodes[id]?.issues?.length ?? 0) > 0}
          subLabelOf={entitySubLabel}
          edges={gridEdges}
          onPressNode={(id) => setPlatformModalFor(id)}
        />
        <ExternalInfraStrip />
        <PlatformModal visible={!!platformModalFor} platformId={platformModalFor} onClose={() => setPlatformModalFor(null)} />
      </View>
    );
  }

  return (
    <View style={{ width, alignSelf: 'center' }}>
      {!dense ? (
        <View style={styles.arrangeBar}>
          <Pressable
            style={styles.arrangeBtn}
            onPress={() => {
              if (rawGroups.length === 0) return;
              const seed = ((Date.now() ^ Math.floor(Math.random() * 0xffffffff)) >>> 0) || 1;
              const fresh = pickCleanRandomAssignment({ rawGroups, machineRowEligible, r, width, height, edges: edgesForScoring, seed });
              setAssignment(fresh);
              if (arrangementHash) saveArrangement(arrangementHash, fresh);
              setArrangementGen((g) => g + 1);
            }}
          >
            <Text style={styles.arrangeBtnText}>Shuffle</Text>
          </Pressable>
          <Pressable
            style={styles.arrangeBtn}
            onPress={() => {
              if (rawGroups.length === 0) return;
              const canon = canonicalAssignment(rawGroups);
              setAssignment(canon);
              if (arrangementHash) clearArrangement(arrangementHash);
              setArrangementGen((g) => g + 1);
              setRelocateMode(false);
              setDragState(null);
            }}
          >
            <Text style={styles.arrangeBtnText}>Reset to auto layout</Text>
          </Pressable>
          <Pressable
            style={[styles.arrangeBtn, relocateMode && styles.arrangeBtnActive]}
            onPress={() => {
              setRelocateMode((v) => !v);
              setDragState(null);
            }}
          >
            <Text style={styles.arrangeBtnText}>{relocateMode ? 'Relocate: ON' : 'Relocate'}</Text>
          </Pressable>
          {relocateMode && layoutOverlapCount > 0 ? (
            <View style={styles.arrangeWarning}>
              <Text style={styles.arrangeWarningText}>Pipelines overlapping -- try moving a service</Text>
            </View>
          ) : null}
        </View>
      ) : null}
      <ZoomPanCanvas
        ref={zoomRef}
        viewportWidth={width}
        viewportHeight={height}
        worldWidth={canvasWidth}
        worldHeight={canvasHeight}
        gestureEnabled={!relocateMode}
        onTouchActive={onTouchActive}
        onViewportChange={(v) => {
          scaleRef.current = v.scale;
        }}
      >
      <Svg width={canvasWidth} height={canvasHeight} viewBox={`0 0 ${canvasWidth} ${canvasHeight}`}>
        <G>
          {machineBoxes.map((box) => {
            const { title, detail, online, state } = machineBoxLabel(box.machineId, Math.floor((box.w - 24) / 13));
            const stroke = state === 'online' ? colors.good : state === 'pending' ? colors.unknown : colors.textMuted;
            const dash = state === 'online' ? undefined : state === 'pending' ? '2,4' : '6,4';
            const bdx = dragState?.kind === 'machine' && dragState.machineId === box.machineId ? dragState.dx : 0;
            const bdy = dragState?.kind === 'machine' && dragState.machineId === box.machineId ? dragState.dy : 0;
            const bx = box.x + bdx;
            const by = box.y + bdy;
            const boxDragProps = relocateMode
              ? makeDragProps({
                  onStart: (clientX, clientY) =>
                    setDragState({ kind: 'machine', machineId: box.machineId, dx: 0, dy: 0, lastClientX: clientX, lastClientY: clientY }),
                  onMove: (clientX, clientY) =>
                    setDragState((cur) => {
                      if (!cur || cur.kind !== 'machine' || cur.machineId !== box.machineId) return cur;
                      const s = scaleRef.current || 1;
                      return {
                        ...cur,
                        dx: cur.dx + (clientX - cur.lastClientX) / s,
                        dy: cur.dy + (clientY - cur.lastClientY) / s,
                        lastClientX: clientX,
                        lastClientY: clientY,
                      };
                    }),
                  onEnd: () => {
                    setDragState((cur) => {
                      if (!cur || cur.kind !== 'machine' || cur.machineId !== box.machineId || !assignment) return null;
                        const dropPoint = { x: box.x + cur.dx + box.w / 2, y: box.y + cur.dy + box.h / 2 };
                        const others = machineBoxes.filter((b) => b.machineId !== box.machineId);
                        const next = reorderMachine(assignment, box.machineId, dropPoint, others);
                        setAssignment(next);
                      if (arrangementHash) saveArrangement(arrangementHash, next);
                      return null;
                    });
                  },
                })
              : svgTapProps(() => setMachineModalFor(box.machineId));
            return (
              <G key={`machine-box-${box.machineId}`} opacity={online ? 1 : 0.6} {...boxDragProps}>
                <Rect
                  x={bx}
                  y={by}
                  width={box.w}
                  height={box.h}
                  rx={14}
                  fill="transparent"
                  stroke={stroke}
                  strokeWidth={state === 'online' ? 2 : 1.5}
                  strokeDasharray={dash}
                />
                <SvgText x={bx + 12} y={by + 26} fontSize={18} fontWeight="500" fill={colors.textPrimary} letterSpacing={1}>
                  {title}
                </SvgText>
                <SvgText x={bx + 12} y={by + 44} fontSize={14} fill={colors.textMuted} letterSpacing={1}>
                  {detail}
                </SvgText>
              </G>
            );
          })}
        </G>

        <G opacity={dragState ? 0.3 : 1}>
          {renderLanes('lines')}
          {renderLanes('labels')}
        </G>

        {nodelessStrip ? (
          <G>
            <Rect
              x={nodelessStrip.x}
              y={nodelessStrip.y}
              width={nodelessStrip.w}
              height={nodelessStrip.h}
              rx={16 * K}
              fill="transparent"
              stroke={nodelessBorder}
              strokeWidth={2.5 * K}
              strokeDasharray={`${8 * K},${5 * K}`}
            />
            <SvgText x={nodelessStrip.x + nodelessStrip.w / 2} y={nodelessStrip.y + 28 * K} textAnchor="middle" fontSize={17 * K} fontWeight="800" fill={colors.textSecondary} letterSpacing={1}>
              MACHINES WITHOUT NODES
            </SvgText>
            <SvgText x={nodelessStrip.x + nodelessStrip.w / 2} y={nodelessStrip.y + 48 * K} textAnchor="middle" fontSize={13 * K} fill={colors.textMuted}>
              no services configured
            </SvgText>
            {nodelessMachineIds.map((machineId, i) => {
              const { title, detail, online, state } = machineBoxLabel(machineId, Math.floor((nodelessStrip.cardW - 28 * K) / (15 * K)));
              const info = machines[machineId];
              const stroke = state === 'online' ? colors.good : state === 'pending' ? colors.unknown : colors.textMuted;
              const dash = state === 'online' ? undefined : state === 'pending' ? '2,4' : '6,4';
              const cx = nodelessStrip.x + nodelessStrip.pad;
              const cy = nodelessStrip.y + nodelessStrip.header + i * nodelessStrip.slot;
              const scheduler = info?.machine_scheduler ? `scheduler ${(info.scheduler_state ?? 'unknown').replace('_', ' ')}` : null;
              return (
                <G key={`nodeless-${machineId}`} opacity={online ? 1 : 0.6} {...svgTapProps(() => setMachineModalFor(machineId))}>
                  <Rect
                    x={cx}
                    y={cy}
                    width={nodelessStrip.cardW}
                    height={nodelessStrip.cardH}
                    rx={12 * K}
                    fill="transparent"
                    stroke={stroke}
                    strokeWidth={(state === 'online' ? 2.5 : 2) * K}
                    strokeDasharray={dash}
                  />
                  <SvgText x={cx + 14 * K} y={cy + 34 * K} fontSize={22 * K} fontWeight="500" fill={colors.textPrimary} letterSpacing={1}>
                    {title}
                  </SvgText>
                  <SvgText x={cx + 14 * K} y={cy + 60 * K} fontSize={15 * K} fill={colors.textMuted} letterSpacing={0.5}>
                    {detail}
                  </SvgText>
                  {scheduler || info?.machine_ip ? (
                    <SvgText x={cx + 14 * K} y={cy + 84 * K} fontSize={14 * K} fill={colors.textMuted}>
                      {[info?.machine_ip, scheduler].filter(Boolean).join(' · ')}
                    </SvgText>
                  ) : null}
                </G>
              );
            })}
          </G>
        ) : null}

        {strip ? (
          <G>
            <Rect
              x={strip.x}
              y={strip.y}
              width={strip.w}
              height={strip.h}
              rx={16 * K}
              fill="transparent"
              stroke={servicesBorder}
              strokeWidth={2.5 * K}
              strokeDasharray={`${8 * K},${5 * K}`}
            />
            <SvgText x={strip.x + strip.w / 2} y={strip.y + 28 * K} textAnchor="middle" fontSize={17 * K} fontWeight="800" fill={colors.textSecondary} letterSpacing={1}>
              EXTERNAL SERVICES
            </SvgText>
            <SvgText x={strip.x + strip.w / 2} y={strip.y + 48 * K} textAnchor="middle" fontSize={13 * K} fill={colors.textMuted}>
              no machine
            </SvgText>
            {servicePseudoIds.map((pid, i) => {
              const svc = estateServices[i];
              const pos = strip.positions[pid];
              if (!pos || !svc) return null;
              const color =
                svc.status === 'ok' ? colors.good : svc.status === 'down' ? colors.bad : svc.status === 'blocked' ? colors.blocked : colors.unknown;
              const statusText =
                svc.status === 'ok'
                  ? 'confirmed healthy'
                  : svc.status === 'down'
                    ? 'confirmed broken'
                    : svc.status === 'blocked'
                      ? 'blocked'
                      : 'unknown -- not reporting';
              const { cardW, cardH } = strip;
              return (
                <G key={pid}>
                  <Rect
                    x={pos.x - cardW / 2}
                    y={pos.y - cardH / 2}
                    width={cardW}
                    height={cardH}
                    rx={12 * K}
                    fill={colors.surface}
                    stroke={color}
                    strokeWidth={2.5 * K}
                    strokeDasharray={`${6 * K},${4 * K}`}
                  />
                  <SvgText x={pos.x} y={pos.y - 8 * K} textAnchor="middle" fontSize={20 * K} fontWeight="800" fill={colors.textPrimary}>
                    {svc.node_name}
                  </SvgText>
                  <SvgText x={pos.x} y={pos.y + 14 * K} textAnchor="middle" fontSize={14 * K} fontWeight="700" fill={color}>
                    {statusText}
                  </SvgText>
                  <SvgText x={pos.x} y={pos.y + cardH / 2 + 20 * K} textAnchor="middle" fontSize={13 * K} fill={colors.textMuted}>
                    {svc.node_type} · {svc.node_platform}
                  </SvgText>
                </G>
              );
            })}
          </G>
        ) : null}

        {isPhone && dense
          ? null
          : platformIds.map((platformId) => {
              const mid = platformMachineMap[platformId] ?? null;
              const canDrag = relocateMode && mid != null;
              const ownBox = mid != null ? machineBoxes.find((bx) => bx.machineId === mid) : undefined;
              const rawSdx = dragState?.kind === 'service' && dragState.platformId === platformId ? dragState.dx : 0;
              const rawSdy = dragState?.kind === 'service' && dragState.platformId === platformId ? dragState.dy : 0;
              const clampTo = (v: number, lo: number, hi: number) => (hi < lo ? (lo + hi) / 2 : Math.min(hi, Math.max(lo, v)));
              const px0 = positions[platformId].x;
              const py0 = positions[platformId].y;
              const sdx = ownBox && rawSdx !== 0 ? clampTo(px0 + rawSdx, ownBox.x + r, ownBox.x + ownBox.w - r) - px0 : rawSdx;
              const sdy = ownBox && rawSdy !== 0 ? clampTo(py0 + rawSdy, ownBox.y + r, ownBox.y + ownBox.h - r) - py0 : rawSdy;
              const mdx = dragState?.kind === 'machine' && mid != null && dragState.machineId === mid ? dragState.dx : 0;
              const mdy = dragState?.kind === 'machine' && mid != null && dragState.machineId === mid ? dragState.dy : 0;
              const dragHandleProps = canDrag
                ? makeDragProps({
                    onStart: (clientX, clientY) =>
                      setDragState({ kind: 'service', machineId: mid as number, platformId, dx: 0, dy: 0, lastClientX: clientX, lastClientY: clientY }),
                    onMove: (clientX, clientY) =>
                      setDragState((cur) => {
                        if (!cur || cur.kind !== 'service' || cur.platformId !== platformId) return cur;
                        const s = scaleRef.current || 1;
                        return {
                          ...cur,
                          dx: cur.dx + (clientX - cur.lastClientX) / s,
                          dy: cur.dy + (clientY - cur.lastClientY) / s,
                          lastClientX: clientX,
                          lastClientY: clientY,
                        };
                      }),
                    onEnd: () => {
                      setDragState((cur) => {
                        if (!cur || cur.kind !== 'service' || cur.platformId !== platformId || !assignment) return null;
                        const siblingIds = (rawGroups.find((g) => g.machineId === mid)?.platformIds ?? []).filter((id) => id !== platformId);
                        const pts = siblingIds.map((id) => positions[id]).filter((p): p is { x: number; y: number } => !!p);
                        const rangeX = pts.length ? Math.max(...pts.map((p) => p.x)) - Math.min(...pts.map((p) => p.x)) : 0;
                        const rangeY = pts.length ? Math.max(...pts.map((p) => p.y)) - Math.min(...pts.map((p) => p.y)) : 0;
                        const orient: 'row' | 'column' = rangeX >= rangeY ? 'row' : 'column';
                        const dropAxisValue = orient === 'row' ? positions[platformId].x + cur.dx : positions[platformId].y + cur.dy;
                        const siblingAxisValue = (id: string) => (orient === 'row' ? positions[id].x : positions[id].y);
                        const next = reorderService(assignment, mid as number, platformId, dropAxisValue, orient, siblingAxisValue);
                        setAssignment(next);
                        if (arrangementHash) saveArrangement(arrangementHash, next);
                        return null;
                      });
                    },
                  })
                : undefined;
              return (
                <PlatformEntity
                  key={platformId}
                  cx={positions[platformId].x + sdx + mdx}
                  cy={positions[platformId].y + sdy + mdy}
                  r={r}
                  label={labelOf[platformId] ?? platformId}
                  state={entityState(platformId)}
                  type={typeOf[platformId]}
                  hasIssues={(topologyNodes[platformId]?.issues?.length ?? 0) > 0}
                  subLabel={entitySubLabel(platformId)}
                  pulse={!dense}
                  onPress={canDrag ? undefined : () => setPlatformModalFor(platformId)}
                  dragHandleProps={dragHandleProps}
                />
              );
            })}
      </Svg>
      </ZoomPanCanvas>

      <BusModal
        visible={!!busModalPair}
        from={busModalPair?.from ?? null}
        to={busModalPair?.to ?? null}
        fromLabel={busModalPair ? titleOf(busModalPair.from) : ''}
        toLabel={busModalPair ? titleOf(busModalPair.to) : ''}
        onClose={() => setBusModalPair(null)}
      />
      <PlatformModal visible={!!platformModalFor} platformId={platformModalFor} onClose={() => setPlatformModalFor(null)} />
      <MachineModal
        visible={machineModalFor != null}
        machineId={machineModalFor}
        onClose={() => setMachineModalFor(null)}
        onSelectPlatform={(id) => {
          setMachineModalFor(null);
          setPlatformModalFor(id);
        }}
      />

      {pickerFor && pickerPipelines.length > 0 ? (
        <View style={styles.pickerWrap}>
          <View style={styles.picker}>
            <Text style={styles.pickerTitle}>{labelOf[pickerFor] ?? pickerFor} pipelines</Text>
            {pickerPipelines.map((p) => (
              <Pressable
                key={p.pipelineId}
                style={styles.pickerRow}
                onPress={() => {
                  setPickerFor(null);
                  onSelectPipeline?.(p.pipelineId);
                }}
              >
                <View style={[styles.pickerDot, { backgroundColor: pipelineHealthColor(p) }]} />
                <Text style={styles.pickerLabel}>
                  {p.name} ({p.health})
                </Text>
              </Pressable>
            ))}
            <Pressable style={styles.pickerClose} onPress={() => setPickerFor(null)}>
              <Text style={styles.pickerCloseText}>Close</Text>
            </Pressable>
          </View>
        </View>
      ) : null}
    </View>
  );
}

