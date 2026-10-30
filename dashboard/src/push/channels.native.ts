import { Platform } from 'react-native';
import * as Notifications from 'expo-notifications';
import {
  channelIdFor,
  DEFAULT_CHANNEL_ID,
  FALLBACK_SOUND,
  KINDS,
  parsePushSound,
  SEVERITIES,
  soundFileFor,
  type PushSoundRef,
} from './sounds';

// 5 groups of 3 long pulses (400 ms on, 120 ms off, 600 ms between groups, about 9.6 s). Android takes no amplitude, so strength is length.
function buildVibrationPattern(groups = 5, pulses = 3, on = 400, off = 120, gap = 600): number[] {
  const out = [0];
  for (let g = 0; g < groups; g++) {
    for (let p = 0; p < pulses; p++) {
      out.push(on);
      if (p < pulses - 1) out.push(off);
      else if (g < groups - 1) out.push(gap);
    }
  }
  return out;
}

export const VIBRATION_PATTERN = buildVibrationPattern();

export async function ensureAlertChannels(): Promise<void> {
  if (Platform.OS !== 'android') return;
  await Promise.all(
    SEVERITIES.flatMap((severity) =>
      KINDS.map((kind) => {
        const ref: PushSoundRef = { severity, kind };
        const first = kind === 'first';
        return Notifications.setNotificationChannelAsync(channelIdFor(ref), {
          name: `Severity ${severity} ${first ? 'alert' : 'reminder'}`,
          importance: first ? Notifications.AndroidImportance.HIGH : Notifications.AndroidImportance.DEFAULT,
          vibrationPattern: VIBRATION_PATTERN,
          lightColor: '#f85149',
          sound: soundFileFor(ref),
          enableVibrate: true,
          lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
        });
      })
    )
  );
  await Notifications.setNotificationChannelAsync(DEFAULT_CHANNEL_ID, {
    name: 'Alerts',
    importance: Notifications.AndroidImportance.HIGH,
    vibrationPattern: VIBRATION_PATTERN,
    lightColor: '#f85149',
    enableVibrate: true,
    lockscreenVisibility: Notifications.AndroidNotificationVisibility.PUBLIC,
  });
}

export async function presentAlert(title: string, body: string, data: Record<string, unknown>): Promise<void> {
  // The relay's registration confirmation targets the default-sound channel, not a severity tone.
  const isConfirmation = data.type === 'push_enabled';
  const ref = parsePushSound(data) ?? FALLBACK_SOUND;
  await Notifications.scheduleNotificationAsync({
    content: {
      title,
      body,
      data,
      ...(Platform.OS === 'ios'
        ? { sound: isConfirmation ? true : soundFileFor(ref), interruptionLevel: 'timeSensitive' as const }
        : {}),
    },
    trigger: Platform.OS === 'android' ? { channelId: isConfirmation ? DEFAULT_CHANNEL_ID : channelIdFor(ref) } : null,
  });
}
