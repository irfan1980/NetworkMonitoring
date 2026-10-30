import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Circle, G, Line, Polygon, Polyline, Rect, Text as SvgText } from 'react-native-svg';
import Animated, {
  cancelAnimation,
  useAnimatedProps,
  useSharedValue,
  withTiming,
  Easing,
} from 'react-native-reanimated';
import { colors } from '../../theme/colors';
import { svgPressProps } from '../../utils/svgPress';
import { FONT_FAMILY } from '../../theme/typography';
import { isQueuedUnknown } from '../../utils/health';
import { countColor, countDimColor } from '../../constants/queueThresholds';
import type { Pair } from '../../types/relay';
import type { LanePlacement } from './laneLayout';
import { buildPath, pathMeta, pointAtPath, tangentAtPath, subPathPoints, pointsToSvg, type PathPoint } from './lanePath';

const AnimatedCircleC = Animated.createAnimatedComponent(Circle);

// Node-side segments and the bus segment get at least nodeMin / busMin world units (bus wins on short lanes, down to 10% per side).
export function laneSplit(len: number, nodeMin = 0, busMin = 0): [number, number] {
  const l = Math.max(1, len);
  const wanted = Math.max(0.24, nodeMin / l);
  const room = (1 - busMin / l) / 2;
  const a = Math.min(0.4, wanted, Math.max(0.1, room));
  return [a, 1 - a];
}

const BROKEN_STUB = 0.09;

const POLL_MS = 5000;

function backlogColor(status: Pair['status']): string {
  if (status === 'red') return colors.bad;
  if (status === 'yellow') return colors.warn;
  return colors.good;
}

function XBadge({ x, y, count, scale, color, compact }: { x: number; y: number; count: number; scale: number; color: string; compact?: boolean }) {
  const label = compact ? `✕ ${count}` : `✕ ${count} failed`;
  const w = Math.max(compact ? 30 : 58, label.length * 7) * scale;
  const h = 20 * scale;
  return (
    <G>
      <Rect x={x - w / 2} y={y - h / 2} width={w} height={h} rx={h / 2} fill={colors.badDim} stroke={color} strokeWidth={1.5} />
      <SvgText x={x} y={y + h * 0.32} fontSize={11 * scale} fontWeight="900" fill={color} textAnchor="middle" fontFamily={FONT_FAMILY}>
        {label}
      </SvgText>
    </G>
  );
}

function arrowheadPoints(px: number, py: number, angle: number, size: number): string {
  const spread = 0.42;
  const backX = px - size * Math.cos(angle);
  const backY = py - size * Math.sin(angle);
  const leftX = backX - size * 0.55 * Math.cos(angle - Math.PI / 2 + spread);
  const leftY = backY - size * 0.55 * Math.sin(angle - Math.PI / 2 + spread);
  const rightX = backX + size * 0.55 * Math.cos(angle - Math.PI / 2 - spread);
  const rightY = backY + size * 0.55 * Math.sin(angle - Math.PI / 2 - spread);
  return `${px},${py} ${leftX},${leftY} ${rightX},${rightY}`;
}

function Packet({ points, cum, color, durationMs, delayMs, scale, onDone }: {
  points: PathPoint[];
  cum: number[];
  color: string;
  durationMs: number;
  delayMs: number;
  scale: number;
  onDone?: () => void;
}) {
  const t = useSharedValue(0);

  useEffect(() => {
    const startId = setTimeout(() => {
      t.value = withTiming(1, { duration: durationMs, easing: Easing.linear });
    }, delayMs);
    const doneId = setTimeout(() => {
      onDone?.();
    }, delayMs + durationMs);
    return () => {
      clearTimeout(startId);
      clearTimeout(doneId);
      cancelAnimation(t);
    };
    // onDone is excluded on purpose: each Packet is created once per burst dot and never updated.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [t, durationMs, delayMs]);

  const animatedProps = useAnimatedProps(() => {
    const p = pointAtPath(points, cum, t.value);
    return {
      cx: p.x,
      cy: p.y,
      opacity: t.value < 0.05 || t.value > 0.95 ? 0 : 1,
    };
  });

  return <AnimatedCircleC r={5 * scale} fill={color} animatedProps={animatedProps} />;
}

interface Segment {
  start: number;
  end: number;
  grey: boolean;
  broken?: boolean;
}

function Pile({
  x,
  y,
  count,
  label,
  color,
  dimColor,
  scale,
  showCaption,
}: {
  x: number;
  y: number;
  count: number | string;
  label: string;
  color: string;
  dimColor: string;
  scale: number;
  showCaption: boolean;
}) {
  const text = String(count);
  const boxH = 17 * scale;
  const boxW = Math.max(21, 12 + 8 * text.length) * scale;
  const step = 3 * scale;
  return (
    <G>
      {[2, 1].map((i) => (
        <Rect
          key={i}
          x={x - boxW / 2 + i * step}
          y={y - boxH / 2 + i * step}
          width={boxW}
          height={boxH}
          rx={2.5 * scale}
          fill={dimColor}
          stroke={color}
          strokeWidth={1.2}
          opacity={0.7}
        />
      ))}
      <Rect x={x - boxW / 2} y={y - boxH / 2} width={boxW} height={boxH} rx={2.5 * scale} fill={colors.background} stroke={color} strokeWidth={1.5} />
      <SvgText x={x} y={y + boxH * 0.33} fontSize={11.5 * scale} fontWeight="900" fill={color} textAnchor="middle" fontFamily={FONT_FAMILY}>
        {text}
      </SvgText>
      {showCaption ? (
        <SvgText x={x} y={y + boxH / 2 + 12 * scale} fontSize={10 * scale} fontWeight="700" fill={color} textAnchor="middle" fontFamily={FONT_FAMILY}>
          {label}
        </SvgText>
      ) : null}
    </G>
  );
}

interface Props {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  viaPoints?: { x: number; y: number }[];
  pair: Pair;
  fromLabel: string;
  toLabel: string;
  scale: number;
  labelIndex: number;
  senderGrey: boolean;
  busGrey: boolean;
  receiverGrey: boolean;
  senderPendingCount?: number | null;
  receiverPendingCount?: number | null;
  receiverBacklog: number;
  onPressQueue?: () => void;
  onPressReceiverBacklog?: () => void;
  onPress?: () => void;
  tick: number;
  missingEnd?: 'sender' | 'receiver' | null;
  missingEndReason?: 'disabled' | 'unconfigured' | null;
  layer?: 'all' | 'lines' | 'labels';
  placement?: LanePlacement;
  nodeMin?: number;
  busMin?: number;
  splits?: [number, number];
  endArrow?: boolean;
  labelText?: string;
}

function PairLaneImpl({
  x1,
  y1,
  x2,
  y2,
  viaPoints,
  pair,
  fromLabel,
  toLabel,
  scale,
  labelIndex,
  senderGrey,
  busGrey,
  receiverGrey,
  senderPendingCount,
  receiverPendingCount,
  receiverBacklog,
  onPress,
  tick,
  missingEnd,
  missingEndReason,
  layer = 'all',
  placement,
  nodeMin,
  busMin,
  splits,
  endArrow,
  labelText,
}: Props) {
  const drawLines = layer !== 'labels';
  const drawLabels = layer !== 'lines';
  const missingSender = missingEnd === 'sender';
  const missingReceiver = missingEnd === 'receiver';
  const showBrokenStub = missingEndReason !== 'disabled' && missingEndReason !== 'unconfigured';
  const sendHalfAbsent = missingSender && missingEndReason === 'unconfigured';
  const recvHalfAbsent = missingReceiver && missingEndReason === 'unconfigured';
  const busDrawn = pair.bus != null;
  const path = useMemo(() => buildPath(x1, y1, x2, y2, viaPoints), [x1, y1, x2, y2, viaPoints]);
  const { cum, total: len } = useMemo(() => pathMeta(path), [path]);
  const [SB1, SB2] = splits ?? laneSplit(len, nodeMin, busMin);
  const pointAt = useCallback((t: number) => pointAtPath(path, cum, t), [path, cum]);
  const tangentAt = useCallback((t: number) => tangentAtPath(path, cum, t), [path, cum]);
  const perpPoint = useCallback(
    (t: number, mag: number, side: number) => {
      const p = pointAtPath(path, cum, t);
      const { ux, uy } = tangentAtPath(path, cum, t);
      return { x: p.x - uy * mag * side, y: p.y + ux * mag * side };
    },
    [path, cum]
  );

  const backlog = backlogColor(pair.status);
  const hasIssues = !!pair.issues && pair.issues.length > 0;
  const senderStuck = !senderGrey && busGrey && pair.pendingOutbox > 0;
  const senderColor = senderStuck ? countColor(pair.pendingOutbox, backlog) : backlog;
  const segments: Segment[] = [
    { start: 0, end: SB1, grey: senderGrey, broken: missingSender },
    { start: SB1, end: SB2, grey: busGrey, broken: !busDrawn },
    { start: SB2, end: 1, grey: receiverGrey, broken: missingReceiver },
  ];
  const segmentColors = [senderColor, backlog, backlog];
  const segmentDownColors: (string | null)[] = [
    senderPendingCount != null ? countColor(senderPendingCount, colors.unknown) : null,
    null,
    receiverPendingCount != null ? countColor(receiverPendingCount, colors.unknown) : null,
  ];

  const segmentArrows = useMemo(
    () =>
      segments.map((seg) => {
        const segLen = (seg.end - seg.start) * len;
        const count = Math.max(2, Math.min(5, Math.round(segLen / 70)));
        const inset = (seg.end - seg.start) * 0.12;
        const usable = seg.end - seg.start - inset * 2;
        return Array.from({ length: count }, (_, i) => seg.start + inset + (usable * (i + 1)) / (count + 1));
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [len, SB1, SB2, senderGrey, busGrey, receiverGrey, pair.status, missingSender, missingReceiver, busDrawn]
  );

  const packetRange = useMemo(() => {
    let best: { start: number; end: number } | null = null;
    let cur: { start: number; end: number } | null = null;
    for (const seg of segments) {
      if (seg.grey || seg.broken) {
        cur = null;
        continue;
      }
      cur = cur ? { start: cur.start, end: seg.end } : { start: seg.start, end: seg.end };
      if (!best || cur.end - cur.start > best.end - best.start) best = cur;
    }
    return best;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [senderGrey, busGrey, receiverGrey, missingSender, missingReceiver, busDrawn]);

  const prevPileRef = useRef<{ tick: number; pendingOutbox: number; queued: number; receiverBacklog: number } | null>(null);
  const pileOrigin = useMemo(() => {
    const prev = prevPileRef.current;
    const currQueued = pair.queued ?? 0;
    let origin: 'sender' | 'bus' | 'receiver' | null = null;
    if (prev && prev.tick !== tick) {
      if (prev.queued - currQueued > 0) origin = 'bus';
      else if (prev.pendingOutbox - pair.pendingOutbox > 0) origin = 'sender';
      else if (prev.receiverBacklog - receiverBacklog > 0) origin = 'receiver';
    }
    prevPileRef.current = { tick, pendingOutbox: pair.pendingOutbox, queued: currQueued, receiverBacklog };
    return origin;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick]);

  const originStart = pileOrigin === 'bus' ? SB1 : pileOrigin === 'receiver' ? SB2 : 0;
  const effectiveRange = useMemo(() => {
    if (!packetRange) return null;
    const start = Math.max(packetRange.start, originStart);
    if (start >= packetRange.end) return null;
    return { start, end: packetRange.end };
  }, [packetRange, originStart]);

  const congestionPenalty = Math.min(2.2, 1 + (pair.queued ?? 0) / 20);
  const baseDuration = POLL_MS * congestionPenalty;
  const flowing = pair.packets > 0 && effectiveRange !== null;
  const packetCount = flowing ? Math.max(1, Math.min(4, pair.packets)) : 0;
  const [activePackets, setActivePackets] = useState<
    Array<{ id: string; points: PathPoint[]; cum: number[]; durationMs: number; delayMs: number }>
  >([]);

  useEffect(() => {
    if (!flowing || !effectiveRange) return;
    const subPts = subPathPoints(path, cum, effectiveRange.start, effectiveRange.end);
    const subCum = pathMeta(subPts).cum;
    const n = packetCount;
    const spawned = Array.from({ length: n }, (_, i) => ({
      id: `${tick}-${i}`,
      points: subPts,
      cum: subCum,
      durationMs: baseDuration,
      delayMs: Math.min(300, baseDuration / (n + 1)) * i,
    }));
    setActivePackets((prev) => {
      const merged = prev.concat(spawned);
      const MAX_ACTIVE = 24;
      return merged.length > MAX_ACTIVE ? merged.slice(merged.length - MAX_ACTIVE) : merged;
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tick]);

  const handlePacketDone = useCallback((id: string) => {
    setActivePackets((prev) => (prev.some((p) => p.id === id) ? prev.filter((p) => p.id !== id) : prev));
  }, []);

  const shortLane = len < 320;
  const showCaption = len >= 240 && scale >= 0.55;
  const pileSide = -1;
  const innerSide = 1;
  const pileMag = shortLane ? 11 : 24 + (labelIndex % 3) * 11;
  const pendingT = shortLane ? 0.18 : 0.22;
  const pendingAnchor = pointAt(pendingT);
  const pendingPos = placement?.pending ?? perpPoint(pendingT, pileMag * scale, pileSide);
  const queueT = 0.6;
  const queueAnchor = pointAt(queueT);
  const queuePos = placement?.queued ?? perpPoint(queueT, pileMag * scale, pileSide);
  const lostMag = pileMag + 4;
  const lostPos = placement?.lost ?? perpPoint(queueT, lostMag * scale, innerSide);
  const receiverBacklogT = shortLane ? 0.86 : 0.83;
  const receiverBacklogAnchor = pointAt(receiverBacklogT);
  const receiverBacklogPos = placement?.backlog ?? perpPoint(receiverBacklogT, pileMag * scale, pileSide);

  const failedOutbox = pair.failedOutbox ?? 0;
  const failedInbox = pair.failedInbox ?? 0;
  const failedMag = (shortLane ? 12 : 20 + (labelIndex % 3) * 9) * scale;
  const failedOutboxT = 0.28;
  const failedOutboxAnchor = pointAt(failedOutboxT);
  const failedOutboxPos = placement?.failedOutbox ?? perpPoint(failedOutboxT, failedMag, innerSide);
  const failedInboxT = 0.72;
  const failedInboxAnchor = pointAt(failedInboxT);
  const failedInboxPos = placement?.failedInbox ?? perpPoint(failedInboxT, failedMag, innerSide);

  const labelT = 0.38 + (labelIndex % 4) * 0.05;
  const labelMag = shortLane ? 10 + (labelIndex % 3) * 2 : 16 + (labelIndex % 3) * 15;
  const labelAnchor = pointAt(labelT);
  const labelPos = placement?.label ?? perpPoint(labelT, labelMag * scale, innerSide);

  return (
    // pointerEvents on a bare <Line> throws in react-native-svg on web; press goes on the group.
    <G {...svgPressProps(onPress)}>
      {onPress && drawLines ? (
        <G>
          <Polyline
            points={pointsToSvg(path)}
            stroke="transparent"
            fill="none"
            strokeWidth={Math.max(28, 22 * scale)}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
          {[pendingPos, queuePos, receiverBacklogPos, labelPos, failedOutboxPos, failedInboxPos].map((p, i) => (
            <Circle key={`hit-${i}`} cx={p.x} cy={p.y + (i < 3 ? 10 * scale : 0)} r={Math.max(26, 26 * scale)} fill="transparent" />
          ))}
        </G>
      ) : null}

      {drawLines && segments.map((seg, i) => {
        if (seg.broken) return null;
        const segPts = subPathPoints(path, cum, seg.start, seg.end);
        const downColor = segmentDownColors[i];
        const color = seg.grey ? downColor ?? colors.unknown : segmentColors[i];
        const opacity = seg.grey ? (downColor ? 0.7 : 0.3) : 0.7;
        return (
          <Polyline
            key={i}
            points={pointsToSvg(segPts)}
            stroke={color}
            fill="none"
            strokeWidth={3 * scale}
            strokeDasharray={`${12 * scale},${10 * scale}`}
            strokeLinejoin="round"
            opacity={opacity}
          />
        );
      })}

      {drawLines && segmentArrows.map((ts, segIdx) => {
        if (segments[segIdx].broken) return null;
        const downColor = segmentDownColors[segIdx];
        const color = segments[segIdx].grey ? downColor ?? colors.unknown : segmentColors[segIdx];
        const opacity = segments[segIdx].grey ? (downColor ? 0.85 : 0.35) : 0.85;
        return ts.map((t, i) => {
          const p = pointAt(t);
          const tang = tangentAt(t);
          return (
            <Polygon
              key={`${segIdx}-${i}`}
              points={arrowheadPoints(p.x, p.y, Math.atan2(tang.uy, tang.ux), 9 * scale)}
              fill={color}
              opacity={opacity}
            />
          );
        });
      })}

      {drawLines && endArrow ? (
        <Polygon
          points={arrowheadPoints(pointAt(0.999).x, pointAt(0.999).y, Math.atan2(tangentAt(0.999).uy, tangentAt(0.999).ux), 13 * scale)}
          fill={receiverGrey ? colors.unknown : backlog}
          opacity={0.95}
        />
      ) : null}

      {drawLines && missingSender && showBrokenStub ? (
        <G>
          <Line
            x1={pointAt(SB1 - BROKEN_STUB).x}
            y1={pointAt(SB1 - BROKEN_STUB).y}
            x2={pointAt(SB1).x}
            y2={pointAt(SB1).y}
            stroke={colors.brokenEnd}
            strokeWidth={3 * scale}
            strokeDasharray={`${6 * scale},${5 * scale}`}
            opacity={0.9}
          />
          {(() => {
            const p = pointAt(SB1 - BROKEN_STUB);
            const s = 6 * scale;
            return (
              <G>
                <Line x1={p.x - s} y1={p.y - s} x2={p.x + s} y2={p.y + s} stroke={colors.brokenEnd} strokeWidth={2.5 * scale} />
                <Line x1={p.x - s} y1={p.y + s} x2={p.x + s} y2={p.y - s} stroke={colors.brokenEnd} strokeWidth={2.5 * scale} />
              </G>
            );
          })()}
        </G>
      ) : null}

      {drawLines && missingReceiver && showBrokenStub ? (
        <G>
          <Line
            x1={pointAt(SB2).x}
            y1={pointAt(SB2).y}
            x2={pointAt(SB2 + BROKEN_STUB).x}
            y2={pointAt(SB2 + BROKEN_STUB).y}
            stroke={colors.brokenEnd}
            strokeWidth={3 * scale}
            strokeDasharray={`${6 * scale},${5 * scale}`}
            opacity={0.9}
          />
          {(() => {
            const p = pointAt(SB2 + BROKEN_STUB);
            const s = 6 * scale;
            return (
              <G>
                <Line x1={p.x - s} y1={p.y - s} x2={p.x + s} y2={p.y + s} stroke={colors.brokenEnd} strokeWidth={2.5 * scale} />
                <Line x1={p.x - s} y1={p.y + s} x2={p.x + s} y2={p.y - s} stroke={colors.brokenEnd} strokeWidth={2.5 * scale} />
              </G>
            );
          })()}
        </G>
      ) : null}

      {drawLines && [SB1, SB2].map((t, i) => {
        const p = pointAt(t);
        const { ux: tux, uy: tuy } = tangentAt(t);
        if ((i === 0 && sendHalfAbsent) || (i === 1 && recvHalfAbsent)) return null;
        const isOpenEnd = (i === 0 && missingSender && missingEndReason === 'disabled') || (i === 1 && missingReceiver && missingEndReason === 'disabled');
        const bg = busDrawn && busGrey;
        const grey = i === 0 ? senderGrey || bg : bg || receiverGrey;
        const downColor = i === 0 ? segmentDownColors[0] : segmentDownColors[2];
        const color = isOpenEnd ? colors.bad : grey ? downColor ?? colors.unknown : backlog;
        const tickLen = 9 * scale;
        return (
          <Line
            key={t}
            x1={p.x - tuy * tickLen}
            y1={p.y + tux * tickLen}
            x2={p.x + tuy * tickLen}
            y2={p.y - tux * tickLen}
            stroke={color}
            strokeWidth={3 * scale}
            opacity={isOpenEnd ? 1 : grey ? 0.5 : 0.9}
          />
        );
      })}

      {drawLabels ? (
      <SvgText
        x={labelPos.x}
        y={labelPos.y}
        fontSize={11 * scale}
        fontWeight="800"
        fill={senderGrey ? colors.unknown : senderColor}
        fontFamily={FONT_FAMILY}
      >
        {labelText ?? `${fromLabel} →`}
      </SvgText>
      ) : null}

      {drawLines && activePackets.map((p) => (
        <Packet
          key={p.id}
          points={p.points}
          cum={p.cum}
          color={colors.packet}
          durationMs={p.durationMs}
          delayMs={p.delayMs}
          scale={scale}
          onDone={() => handlePacketDone(p.id)}
        />
      ))}

      {drawLabels && pair.pendingOutbox > 0 ? (
        <Pile
          x={pendingPos.x}
          y={pendingPos.y}
          count={pair.pendingOutbox}
          label="to send"
          showCaption={placement?.pending?.caption ?? showCaption}
          color={senderGrey ? colors.unknown : countColor(pair.pendingOutbox)}
          dimColor={countDimColor(pair.pendingOutbox)}
          scale={scale}
        />
      ) : null}

      {!drawLabels ? null : isQueuedUnknown(pair) ? (
        <Pile x={queuePos.x} y={queuePos.y} count="?" label="checking…" showCaption={placement?.queued?.caption ?? showCaption} color={colors.unknown} dimColor={colors.unknownDim} scale={scale} />
      ) : (pair.queued ?? 0) > 0 ? (
        <Pile
          x={queuePos.x}
          y={queuePos.y}
          count={pair.queued ?? 0}
          label="queued"
          showCaption={placement?.queued?.caption ?? showCaption}
          color={busGrey ? colors.unknown : countColor(pair.queued)}
          dimColor={countDimColor(pair.queued)}
          scale={scale}
        />
      ) : null}

      {drawLabels && pair.lost > 0 ? (
        <G>
          <Rect
            x={lostPos.x - 30 * scale}
            y={lostPos.y - 10 * scale}
            width={60 * scale}
            height={20 * scale}
            rx={10 * scale}
            fill={colors.badDim}
            stroke={colors.bad}
            strokeWidth={1.5}
          />
          <SvgText x={lostPos.x} y={lostPos.y + 4 * scale} fontSize={11 * scale} fontWeight="900" fill={colors.bad} textAnchor="middle" fontFamily={FONT_FAMILY}>
            lost {pair.lost}
          </SvgText>
        </G>
      ) : null}

      {drawLabels && receiverBacklog > 0 ? (
        <Pile
          x={receiverBacklogPos.x}
          y={receiverBacklogPos.y}
          count={receiverBacklog}
          label="to apply"
          showCaption={placement?.backlog?.caption ?? showCaption}
          color={receiverGrey ? colors.unknown : countColor(receiverBacklog)}
          dimColor={countDimColor(receiverBacklog)}
          scale={scale}
        />
      ) : null}

      {drawLabels && failedOutbox > 0 ? (
        <XBadge x={failedOutboxPos.x} y={failedOutboxPos.y} count={failedOutbox} scale={scale} color={colors.bad} compact={shortLane} />
      ) : null}
      {drawLabels && failedInbox > 0 ? (
        <XBadge x={failedInboxPos.x} y={failedInboxPos.y} count={failedInbox} scale={scale} color={colors.bad} compact={shortLane} />
      ) : null}

      {drawLabels && hasIssues ? (
        (() => {
          const p = pointAt(0.5);
          const rad = Math.max(8, 9 * scale);
          return (
            <G>
              <Circle cx={p.x} cy={p.y} r={rad} fill={colors.warn} stroke={colors.background} strokeWidth={2} />
              <SvgText x={p.x} y={p.y + rad * 0.34} fontSize={rad * 1.2} fontWeight="900" fill={colors.background} textAnchor="middle" fontFamily={FONT_FAMILY}>
                !
              </SvgText>
            </G>
          );
        })()
      ) : null}

    </G>
  );
}

export const PairLane = React.memo(PairLaneImpl);
