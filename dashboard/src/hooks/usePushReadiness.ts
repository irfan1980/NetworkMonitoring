import { useCallback, useEffect, useState } from 'react';
import { AppState } from 'react-native';
import { getChannelInfo, getPermissionState } from '../push/pushClient';
import { getBatteryExempt } from '../push/batteryOptimization';
import type { PushChannelInfo, PushPermissionState } from '../types/push';

export interface PushReadiness {
  permission: PushPermissionState | null;
  channel: PushChannelInfo | null;
  batteryExempt: boolean | null;
  refresh: () => void;
}

// Re-read when the app returns to the foreground: the user changes these in system settings.
export function usePushReadiness(): PushReadiness {
  const [permission, setPermission] = useState<PushPermissionState | null>(null);
  const [channel, setChannel] = useState<PushChannelInfo | null>(null);
  const [batteryExempt, setBatteryExempt] = useState<boolean | null>(null);

  const refresh = useCallback(() => {
    void getPermissionState().then(setPermission).catch(() => {});
    void getChannelInfo().then(setChannel).catch(() => {});
    setBatteryExempt(getBatteryExempt());
  }, []);

  useEffect(() => {
    refresh();
    const sub = AppState.addEventListener('change', (s) => {
      if (s === 'active') refresh();
    });
    return () => sub.remove();
  }, [refresh]);

  return { permission, channel, batteryExempt, refresh };
}
