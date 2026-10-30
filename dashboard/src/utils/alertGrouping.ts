import type { AlertMessage } from '../types/relay';

export interface AlertGroup {
  alert: AlertMessage;
  alertIds: string[];
  count: number;
}

export function groupAlerts(alerts: AlertMessage[]): AlertGroup[] {
  const byKey = new Map<string, AlertMessage[]>();
  for (const a of alerts) {
    const perObject = a.rootCause === 'schedule' || a.rootCause === 'scheduler_endpoint';
    const key = perObject ? a.alertId : `${a.pipelineId != null ? String(a.pipelineId) : a.rootCause ?? 'x'}|${a.reason}`;
    if (!byKey.has(key)) byKey.set(key, []);
    byKey.get(key)!.push(a);
  }
  const groups: AlertGroup[] = [];
  for (const list of byKey.values()) {
    const sorted = [...list].sort((a, b) => (a.ts < b.ts ? 1 : a.ts > b.ts ? -1 : 0));
    groups.push({ alert: sorted[0], alertIds: sorted.map((a) => a.alertId), count: sorted.length });
  }
  return groups;
}

export function sortGroupsBySeverityThenRecency(groups: AlertGroup[]): AlertGroup[] {
  return [...groups].sort((a, b) => {
    if (a.alert.severity !== b.alert.severity) return a.alert.severity - b.alert.severity;
    return a.alert.ts < b.alert.ts ? 1 : a.alert.ts > b.alert.ts ? -1 : 0;
  });
}
