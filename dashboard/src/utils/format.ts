export function formatAgeSeconds(seconds: number | null | undefined): string {
  if (seconds == null) return '--';
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  return `${Math.round(seconds / 3600)}h`;
}

export function formatLag(seconds: number | null | undefined): string {
  if (seconds == null) return '--';
  return `${seconds.toFixed(1)}s`;
}

export function formatTimeAgo(iso: string | null | undefined): string {
  if (!iso) return '--';
  const diffMs = Date.now() - new Date(iso).getTime();
  const s = Math.max(0, Math.round(diffMs / 1000));
  return `${formatAgeSeconds(s)} ago`;
}

export function formatClock(iso: string | null | undefined): string {
  if (!iso) return '--';
  const d = new Date(iso);
  return d.toLocaleTimeString();
}

export function formatHeartbeatAge(heartbeatAgeSeconds: number | null | undefined): string {
  if (heartbeatAgeSeconds == null) return 'no ping yet';
  return `${formatAgeSeconds(heartbeatAgeSeconds)} ago`;
}

export function formatDuration(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const mm = h > 0 ? String(m).padStart(2, '0') : String(m);
  return h > 0 ? `${h}:${mm}:${String(s).padStart(2, '0')}` : `${mm}:${String(s).padStart(2, '0')}`;
}

export function formatUtc(iso: string | null | undefined): string {
  if (!iso) return '--';
  return `${iso.replace('T', ' ').slice(0, 16)} UTC`;
}

export function formatTimeUntil(iso: string | null | undefined): string {
  if (!iso) return '--';
  const diffMs = new Date(iso).getTime() - Date.now();
  if (diffMs <= 0) return 'due';
  return `in ${formatAgeSeconds(Math.round(diffMs / 1000))}`;
}
