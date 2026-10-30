const KEY = 'sb_device_id';

export async function loadDeviceId(): Promise<string | null> {
  return typeof localStorage === 'undefined' ? null : localStorage.getItem(KEY);
}

export async function saveDeviceId(id: string): Promise<void> {
  localStorage.setItem(KEY, id);
}
