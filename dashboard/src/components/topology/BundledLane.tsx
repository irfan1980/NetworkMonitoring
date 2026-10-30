import React, { useMemo } from 'react';
import { Circle, G, Line, Polygon, Text as SvgText } from 'react-native-svg';
import { colors } from '../../theme/colors';
import { svgPressProps } from '../../utils/svgPress';
import { countColor, queueLevelForCount } from '../../constants/queueThresholds';
import { FONT_FAMILY } from '../../theme/typography';
import type { Pair } from '../../types/relay';

function priority(status: Pair['status'] | undefined): number {
  if (status === 'red') return 3;
  if (status === 'yellow') return 2;
  if (status === 'green') return 1;
  return 0;
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

function backlogSum(p: Pair | undefined): number {
  if (!p) return 0;
  return (p.pendingOutbox ?? 0) + (p.queued ?? 0) + (p.lost ?? 0) + (p.failedOutbox ?? 0) + (p.failedInbox ?? 0);
}

interface DownPending {
  senderPendingCount?: number | null;
  receiverPendingCount?: number | null;
}

interface Props {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  forward?: Pair;
  backward?: Pair;
  bothDown: boolean;
  hasIssues: boolean;
  scale: number;
  onPress?: () => void;
  fwdGrey?: DownPending;
  bwdGrey?: DownPending;
}

function BundledLaneImpl({ x1, y1, x2, y2, forward, backward, bothDown, hasIssues, scale, onPress, fwdGrey, bwdGrey }: Props) {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const len = Math.max(1, Math.hypot(dx, dy));
  const angle = Math.atan2(dy, dx);
  const pointAt = (t: number) => ({ x: x1 + dx * t, y: y1 + dy * t });

  const worse = priority(forward?.status) >= priority(backward?.status) ? forward : backward;
  const downPendingCount = Math.max(fwdGrey?.senderPendingCount ?? -1, fwdGrey?.receiverPendingCount ?? -1, bwdGrey?.senderPendingCount ?? -1, bwdGrey?.receiverPendingCount ?? -1);
  const downLevel = downPendingCount < 0 ? null : queueLevelForCount(downPendingCount);
  const downPriority = downLevel === 'red' ? 3 : downLevel === 'degraded' || downLevel === 'yellow' ? 2 : 0;
  const worstPriority = Math.max(priority(worse?.status), downPriority);
  const color = bothDown ? colors.unknown : worstPriority === 3 ? colors.bad : worstPriority === 2 ? colors.warn : colors.good;
  const totalBacklog = backlogSum(forward) + backlogSum(backward);
  const badgeColor = bothDown ? colors.unknown : countColor(totalBacklog, colors.good);
  const totalFailed = (forward?.failedOutbox ?? 0) + (forward?.failedInbox ?? 0) + (backward?.failedOutbox ?? 0) + (backward?.failedInbox ?? 0);

  const zonesOf = (p: Pair | undefined, reversed: boolean) => {
    if (!p) return null;
    const senderOn = !(p.missingEnd === 'sender' && p.missingEndReason === 'unconfigured');
    const receiverOn = !(p.missingEnd === 'receiver' && p.missingEndReason === 'unconfigured');
    return {
      low: reversed ? receiverOn : senderOn,
      mid: p.bus != null,
      high: reversed ? senderOn : receiverOn,
      senderOn,
    };
  };
  const fz = zonesOf(forward, false);
  const bz = zonesOf(backward, true);
  const zLow = !!(fz?.low || bz?.low);
  const zMid = !!(fz?.mid || bz?.mid);
  const zHigh = !!(fz?.high || bz?.high);
  const arrowAt = (z: ReturnType<typeof zonesOf>, reversed: boolean) => {
    if (!z || z.mid) return reversed ? 0.62 : 0.38;
    const senderT = reversed ? 0.88 : 0.12;
    return z.senderOn ? senderT : 1 - senderT;
  };

  const arrowPts = useMemo(() => {
    const fwdPt = pointAt(arrowAt(fz, false));
    const bwdPt = pointAt(arrowAt(bz, true));
    return { fwdPt, bwdPt };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [x1, y1, x2, y2, fz?.mid, fz?.senderOn, bz?.mid, bz?.senderOn]);
  const lineStyle = { stroke: color, strokeWidth: 2.5 * scale, strokeDasharray: `${10 * scale},${8 * scale}`, opacity: bothDown ? 0.3 : 0.65 };
  const zoneLine = (a: number, b: number) => {
    const s = pointAt(a);
    const e = pointAt(b);
    return <Line key={a} x1={s.x} y1={s.y} x2={e.x} y2={e.y} {...lineStyle} />;
  };

  const mid = pointAt(0.5);

  const openEndTick = (t: number) => {
    const p = pointAt(t);
    const tickLen = 7 * scale;
    const ux = dx / len;
    const uy = dy / len;
    return { x1: p.x - uy * tickLen, y1: p.y + ux * tickLen, x2: p.x + uy * tickLen, y2: p.y - ux * tickLen };
  };
  const forwardOpenEnd = forward?.missingEndReason === 'disabled' ? (forward.missingEnd === 'sender' ? openEndTick(0.08) : forward.missingEnd === 'receiver' ? openEndTick(0.92) : null) : null;
  const backwardOpenEnd = backward?.missingEndReason === 'disabled' ? (backward.missingEnd === 'sender' ? openEndTick(0.92) : backward.missingEnd === 'receiver' ? openEndTick(0.08) : null) : null;

  return (
    // Handler on the group: pointerEvents on a bare <Line> throws in react-native-svg on web.
    <G {...svgPressProps(onPress)}>
      {onPress ? (
        <Line x1={x1} y1={y1} x2={x2} y2={y2} stroke="transparent" fill="none" strokeWidth={Math.max(28, 22 * scale)} strokeLinecap="round" />
      ) : null}
      {zLow ? zoneLine(0, 0.24) : null}
      {zMid ? zoneLine(0.24, 0.76) : null}
      {zHigh ? zoneLine(0.76, 1) : null}
      {zLow ? <Line {...openEndTick(0.24)} stroke={color} strokeWidth={2.5 * scale} opacity={bothDown ? 0.4 : 0.9} /> : null}
      {zHigh ? <Line {...openEndTick(0.76)} stroke={color} strokeWidth={2.5 * scale} opacity={bothDown ? 0.4 : 0.9} /> : null}
      {forward ? (
        <Polygon points={arrowheadPoints(arrowPts.fwdPt.x, arrowPts.fwdPt.y, angle, 8 * scale)} fill={color} opacity={bothDown ? 0.35 : 0.85} />
      ) : null}
      {backward ? (
        <Polygon points={arrowheadPoints(arrowPts.bwdPt.x, arrowPts.bwdPt.y, angle + Math.PI, 8 * scale)} fill={color} opacity={bothDown ? 0.35 : 0.85} />
      ) : null}

      {totalBacklog > 0 ? (
        <G>
          <Circle cx={mid.x} cy={mid.y} r={Math.max(11, 12 * scale)} fill={colors.background} stroke={badgeColor} strokeWidth={1.5} />
          <SvgText x={mid.x} y={mid.y + 4 * scale} fontSize={10 * scale} fontWeight="900" fill={badgeColor} textAnchor="middle" fontFamily={FONT_FAMILY}>
            {totalBacklog > 999 ? '999+' : totalBacklog}
          </SvgText>
        </G>
      ) : null}

      {totalFailed > 0 ? (
        <Circle cx={mid.x + 18 * scale} cy={mid.y - 14 * scale} r={Math.max(7, 7 * scale)} fill={colors.bad} stroke={colors.background} strokeWidth={1.5} />
      ) : null}

      {hasIssues ? (
        <Circle cx={mid.x - 18 * scale} cy={mid.y - 14 * scale} r={Math.max(7, 7 * scale)} fill={colors.warn} stroke={colors.background} strokeWidth={1.5} />
      ) : null}

      {forwardOpenEnd ? <Line {...forwardOpenEnd} stroke={colors.bad} strokeWidth={2.5 * scale} opacity={1} /> : null}
      {backwardOpenEnd ? <Line {...backwardOpenEnd} stroke={colors.bad} strokeWidth={2.5 * scale} opacity={1} /> : null}
    </G>
  );
}

export const BundledLane = React.memo(BundledLaneImpl);
