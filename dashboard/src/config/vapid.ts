// A Web Push certificate public key is a 65-byte P-256 point in base64url: 87 chars, starts with "B".
const VAPID_SHAPE = /^B[A-Za-z0-9_-]{85,87}$/;

export function normalizeVapid(raw: unknown): string {
  if (typeof raw !== 'string') return '';
  // Surrounding whitespace/quotes are a common paste artefact.
  return raw.trim().replace(/^["']|["']$/g, '').trim();
}

export function vapidProblem(raw: unknown): string | null {
  if (raw === undefined || raw === null || raw === '') return 'missing';
  if (typeof raw !== 'string') {
    const keys = typeof raw === 'object' ? ` with keys [${Object.keys(raw as object).join(', ')}]` : '';
    return `invalid (must be a plain string, got ${typeof raw}${keys})`;
  }
  const v = normalizeVapid(raw);
  if (!v) return 'missing';
  if (VAPID_SHAPE.test(v)) return null;
  return `invalid (got ${v.length} chars starting with "${v.charAt(0)}"; expected the ~87-char base64url public key starting with "B")`;
}
