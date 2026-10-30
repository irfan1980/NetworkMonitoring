import { Platform } from 'react-native';
import { requireOptionalNativeModule } from 'expo';
import * as IntentLauncher from 'expo-intent-launcher';
import Constants from 'expo-constants';
import AsyncStorage from '@react-native-async-storage/async-storage';

const PROMPTED_KEY = 'sb_battery_prompted';

interface BatteryModule {
  isIgnoringBatteryOptimizations(): boolean;
}

// Null on iOS and on dev builds made before the module existed.
const native = Platform.OS === 'android' ? requireOptionalNativeModule<BatteryModule>('BatteryOptimization') : null;

export const batterySupported = Platform.OS === 'android';

/** true = exempt, false = not exempt, null = unknown (needs a new dev build) or not applicable. */
export function getBatteryExempt(): boolean | null {
  if (!native) return null;
  try {
    return native.isIgnoringBatteryOptimizations();
  } catch {
    return null;
  }
}

export async function requestBatteryExemption(): Promise<void> {
  const packageName = Constants.expoConfig?.android?.package ?? 'com.servicebusmonitoring.app';
  try {
    await IntentLauncher.startActivityAsync(IntentLauncher.ActivityAction.REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, {
      data: `package:${packageName}`,
    });
  } catch {
    // Some OEMs refuse the direct request; the list screen still lets the user pick the app.
    await IntentLauncher.startActivityAsync(IntentLauncher.ActivityAction.IGNORE_BATTERY_OPTIMIZATION_SETTINGS);
  }
}

export async function wasBatteryPrompted(): Promise<boolean> {
  return (await AsyncStorage.getItem(PROMPTED_KEY)) === '1';
}

export async function markBatteryPrompted(): Promise<void> {
  await AsyncStorage.setItem(PROMPTED_KEY, '1');
}
