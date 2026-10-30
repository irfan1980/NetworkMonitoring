import { useMemo } from 'react';
import { DEFAULT_LANE_TUNING, type LaneTuning } from '../config/laneTuning';
import { useConnectionStore } from '../stores/connectionStore';

export function useLaneTuning(): LaneTuning {
  const relay = useConnectionStore((s) => s.laneTuning);
  return useMemo(
    () => ({
      nodeMin: relay?.nodeMin && relay.nodeMin > 0 ? relay.nodeMin : DEFAULT_LANE_TUNING.nodeMin,
      busMin: relay?.busMin && relay.busMin > 0 ? relay.busMin : DEFAULT_LANE_TUNING.busMin,
    }),
    [relay]
  );
}
