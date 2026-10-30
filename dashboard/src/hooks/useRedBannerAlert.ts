import { useMemo } from 'react';
import { useConnectionStore } from '../stores/connectionStore';
import type { AlertMessage } from '../types/relay';

export interface RedBannerAlertState {
  top: AlertMessage | null;
  otherActiveCount: number;
}

export function useRedBannerAlert(): RedBannerAlertState {
  const activeAlerts = useConnectionStore((s) => s.activeAlerts);

  const triggering = useMemo(
    () =>
      Object.values(activeAlerts)
        .filter((a) => !a.acknowledged && a.channels.includes('full_red_screen'))
        .sort((a, b) => a.severity - b.severity),
    [activeAlerts]
  );

  const top = triggering[0] ?? null;

  const activeCount = useMemo(
    () => Object.values(activeAlerts).filter((a) => a.event !== 'resolved' && !a.resolvedAt).length,
    [activeAlerts]
  );

  return {
    top,
    otherActiveCount: Math.max(0, activeCount - (top ? 1 : 0)),
  };
}
