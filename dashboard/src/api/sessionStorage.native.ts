import * as SecureStore from 'expo-secure-store';

const KEY = 'sb_session';

export async function loadSession(): Promise<string | null> {
  return SecureStore.getItemAsync(KEY);
}

export async function saveSession(raw: string): Promise<void> {
  await SecureStore.setItemAsync(KEY, raw);
}

export async function clearSession(): Promise<void> {
  await SecureStore.deleteItemAsync(KEY);
}
