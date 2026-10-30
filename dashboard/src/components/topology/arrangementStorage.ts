import { Platform } from 'react-native';
import type { SlotAssignment } from './arrangement';

const memoryStore = new Map<string, string>();

function webSessionStorage(): Storage | null {
  if (Platform.OS !== 'web') return null;
  try {
    return typeof window !== 'undefined' && window.sessionStorage ? window.sessionStorage : null;
  } catch {
    return null;
  }
}

function keyFor(hash: string): string {
  return `topology-arrangement:v1:${hash}`;
}

export function loadArrangement(hash: string): SlotAssignment | null {
  const key = keyFor(hash);
  const ss = webSessionStorage();
  const raw = ss ? ss.getItem(key) : memoryStore.get(key) ?? null;
  if (!raw) return null;
  try {
    return JSON.parse(raw) as SlotAssignment;
  } catch {
    return null;
  }
}

export function saveArrangement(hash: string, assignment: SlotAssignment): void {
  const key = keyFor(hash);
  const raw = JSON.stringify(assignment);
  const ss = webSessionStorage();
  if (ss) ss.setItem(key, raw);
  else memoryStore.set(key, raw);
}

export function clearArrangement(hash: string): void {
  const key = keyFor(hash);
  const ss = webSessionStorage();
  if (ss) ss.removeItem(key);
  else memoryStore.delete(key);
}
