import { Platform } from 'react-native';
import * as Device from 'expo-device';

function browserName(): string {
  const ua = typeof navigator === 'undefined' ? '' : navigator.userAgent;
  if (/Edg\//.test(ua)) return 'Edge';
  if (/OPR\//.test(ua)) return 'Opera';
  if (/Firefox\//.test(ua)) return 'Firefox';
  if (/Chrome\//.test(ua)) return 'Chrome';
  if (/Safari\//.test(ua)) return 'Safari';
  return 'browser';
}

export function defaultDeviceName(): string {
  if (Platform.OS === 'web') return `web - ${browserName()}`;
  return `${Platform.OS} - ${Device.modelName ?? 'device'}`;
}
