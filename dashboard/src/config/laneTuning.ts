export interface LaneTuning {
  nodeMin: number;
  busMin: number;
}

const fromEnv = (v: string | undefined, fallback: number) => {
  const n = Number(v);
  return v != null && v !== '' && Number.isFinite(n) && n > 0 ? n : fallback;
};

// World units at 1x: min length of a lane's node-side segments and of its service-bus segment.
export const DEFAULT_LANE_TUNING: LaneTuning = {
  nodeMin: fromEnv(process.env.EXPO_PUBLIC_LANE_NODE_MIN, 70),
  busMin: fromEnv(process.env.EXPO_PUBLIC_LANE_BUS_MIN, 150),
};
