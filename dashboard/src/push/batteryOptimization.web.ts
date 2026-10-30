export const batterySupported = false;

export function getBatteryExempt(): boolean | null {
  return null;
}

export async function requestBatteryExemption(): Promise<void> {}

export async function wasBatteryPrompted(): Promise<boolean> {
  return true;
}

export async function markBatteryPrompted(): Promise<void> {}
