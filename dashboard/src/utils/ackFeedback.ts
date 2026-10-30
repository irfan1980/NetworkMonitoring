export const ACK_NOTE = 'Acknowledging switches push reminders to every 30 min for everyone (every 15 min while unacknowledged).';

export function ackFailureText(reason: string): string | null {
  if (reason === 'invalid_pin') return null;
  if (reason === 'rate_limited') return 'Too many wrong PINs. Wait a minute and try again.';
  if (reason === 'unknown_alert' || reason === 'not_found') return 'Alert already cleared.';
  return `Could not acknowledge (${reason}).`;
}

export function ackedByText(by: string | null | undefined, at: string | null | undefined): string {
  const who = by ? ` by ${by}` : '';
  const when = at && !Number.isNaN(new Date(at).getTime()) ? ` · ${new Date(at).toLocaleString()}` : '';
  return `Acknowledged${who}${when}`;
}
