import React, { useMemo, useRef, useState } from 'react';
import { View } from 'react-native';
import Svg, { Line } from 'react-native-svg';
import { colors } from '../../theme/colors';
import { computeGridLayout } from './gridLayout';
import { ZoomPanCanvas, type Viewport, type ZoomPanHandle } from './ZoomPanCanvas';
import { PlatformEntity } from './PlatformEntity';
import type { Pair } from '../../types/relay';

const EDGE_COLOR: Record<Pair['status'], string> = {
  green: colors.good,
  yellow: colors.warn,
  red: colors.bad,
  grey: colors.unknown,
};

export interface GridEdge {
  key: string;
  from: string;
  to: string;
  status: Pair['status'];
}

interface Props {
  width: number;
  height: number;
  platformIds: string[];
  labelOf: Record<string, string>;
  typeOf: Record<string, string>;
  entityState: (id: string) => 'ok' | 'grey' | 'down' | 'yellow' | 'degraded' | 'red';
  hasIssues: (id: string) => boolean;
  subLabelOf?: (id: string) => string | undefined;
  edges: GridEdge[];
  onPressNode?: (id: string) => void;
}

const CULL_MARGIN = 80;

export function GridTopologyView({
  width,
  height,
  platformIds,
  labelOf,
  typeOf,
  entityState,
  hasIssues,
  subLabelOf,
  edges,
  onPressNode,
}: Props) {
  const zoomRef = useRef<ZoomPanHandle>(null);

  const sortedIds = useMemo(() => [...platformIds].sort(), [platformIds]);

  const nodeRadius = platformIds.length > 80 ? 16 : platformIds.length > 30 ? 20 : 26;
  const layout = useMemo(
    () =>
      computeGridLayout(sortedIds, {
        nodeRadius,
        gap: nodeRadius * 1.4,
        aspect: width > 0 && height > 0 ? width / height : 1.6,
      }),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [sortedIds.join(','), nodeRadius, width, height]
  );

  const [viewport, setViewport] = useState<Viewport>({ scale: 1, tx: 0, ty: 0 });

  const visible = useMemo(() => {
    const { scale, tx, ty } = viewport;
    const s = scale || 1;
    const left = -tx / s - CULL_MARGIN;
    const top = -ty / s - CULL_MARGIN;
    const right = left + width / s + CULL_MARGIN * 2;
    const bottom = top + height / s + CULL_MARGIN * 2;

    const visibleNodes = sortedIds.filter((id) => {
      const p = layout.positions[id];
      if (!p) return false;
      return p.x + nodeRadius >= left && p.x - nodeRadius <= right && p.y + nodeRadius >= top && p.y - nodeRadius <= bottom;
    });
    const visibleSet = new Set(visibleNodes);
    const visibleEdges = edges.filter((e) => visibleSet.has(e.from) || visibleSet.has(e.to));
    return { visibleNodes, visibleEdges };
  }, [viewport, layout, sortedIds, edges, width, height, nodeRadius]);

  return (
    <View style={{ width, height }}>
      <ZoomPanCanvas
        ref={zoomRef}
        viewportWidth={width}
        viewportHeight={height}
        worldWidth={Math.max(width, layout.worldWidth)}
        worldHeight={Math.max(height, layout.worldHeight)}
        onViewportChange={setViewport}
      >
        <Svg width={Math.max(width, layout.worldWidth)} height={Math.max(height, layout.worldHeight)}>
          {visible.visibleEdges.map((e) => {
            const a = layout.positions[e.from];
            const b = layout.positions[e.to];
            if (!a || !b) return null;
            return (
              <Line
                key={e.key}
                x1={a.x}
                y1={a.y}
                x2={b.x}
                y2={b.y}
                stroke={EDGE_COLOR[e.status] ?? colors.unknown}
                strokeWidth={e.status === 'red' ? 2.5 : 1.5}
                opacity={e.status === 'grey' ? 0.5 : 0.85}
              />
            );
          })}
          {visible.visibleNodes.map((id) => {
            const p = layout.positions[id];
            return (
              <PlatformEntity
                key={id}
                cx={p.x}
                cy={p.y}
                r={nodeRadius}
                label={labelOf[id] ?? id}
                state={entityState(id)}
                type={typeOf[id]}
                hasIssues={hasIssues(id)}
                subLabel={subLabelOf?.(id)}
                pulse={false}
                onPress={onPressNode ? () => onPressNode(id) : undefined}
              />
            );
          })}
        </Svg>
      </ZoomPanCanvas>
    </View>
  );
}
