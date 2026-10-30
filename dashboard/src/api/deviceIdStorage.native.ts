import * as SecureStore from 'expo-secure-store';

const KEY = 'sb_device_id';

export async function loadDeviceId(): Promise<string | null> {
  return SecureStore.getItemAsync(KEY);
}

export async function saveDeviceId(id: string): Promise<void> {
  await SecureStore.setItemAsync(KEY, id);
}
