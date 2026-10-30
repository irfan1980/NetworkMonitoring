// No secure storage on web; the bearer token is revocable server-side, so localStorage is accepted.
const KEY = 'sb_session';

export async function loadSession(): Promise<string | null> {
  return typeof localStorage === 'undefined' ? null : localStorage.getItem(KEY);
}

export async function saveSession(raw: string): Promise<void> {
  localStorage.setItem(KEY, raw);
}

export async function clearSession(): Promise<void> {
  localStorage.removeItem(KEY);
}
