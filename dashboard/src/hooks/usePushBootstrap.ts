import { useEffect } from 'react';
import { Alert } from 'react-native';
import { useRouter } from 'expo-router';
import { FORCE_FIXTURE } from '../config/env';
import { getPushSupport, setupPushListeners, subscribeTokenRefresh } from '../push/pushClient';
import {
  batterySupported,
  getBatteryExempt,
  markBatteryPrompted,
  requestBatteryExemption,
  wasBatteryPrompted,
} from '../push/batteryOptimization';
import { usePushStore } from '../stores/pushStore';

export function usePushBootstrap() {
  const router = useRouter();
  const init = usePushStore((s) => s.init);
  const syncToken = usePushStore((s) => s.syncToken);
  const registered = usePushStore((s) => s.registration !== null);

  useEffect(() => {
    if (FORCE_FIXTURE) return;
    void init();
    if (!getPushSupport().supported) return;
    const offListeners = setupPushListeners(({ alertId, type }) => {
      if (type === 'push_enabled') router.push('/notifications');
      else if (alertId) router.push({ pathname: '/alerts', params: { alertId } });
    });
    const offRefresh = subscribeTokenRefresh((token) => {
      void syncToken(token).catch(() => {});
    });
    return () => {
      offListeners();
      offRefresh();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // One-time, after the first successful registration.
  useEffect(() => {
    if (!registered || !batterySupported) return;
    let cancelled = false;
    void (async () => {
      if (getBatteryExempt() === true || (await wasBatteryPrompted()) || cancelled) return;
      await markBatteryPrompted();
      Alert.alert(
        'Allow background alerts',
        'Some phones delay or drop notifications for apps that are battery-optimized, so alerts could arrive late or not at all. Allow this app to run without battery optimization so alerts arrive immediately.',
        [
          { text: 'Not now', style: 'cancel' },
          { text: 'Allow', onPress: () => void requestBatteryExemption() },
        ]
      );
    })();
    return () => {
      cancelled = true;
    };
  }, [registered]);
}
