import type { ConfigIssue } from '../types/relay';

export function worstSeverity(issues: ConfigIssue[]): ConfigIssue['severity'] | null {
  if (issues.some((i) => i.severity === 'error')) return 'error';
  if (issues.length > 0) return 'warning';
  return null;
}

export function resolveIssues(codes: string[] | undefined, all: ConfigIssue[]): ConfigIssue[] {
  if (!codes || codes.length === 0) return [];
  const byCode = new Map(all.map((i) => [i.code, i]));
  return codes.map((c) => byCode.get(c)).filter((i): i is ConfigIssue => !!i);
}

export function formatIssueWhere(issue: ConfigIssue, labelOf: (id: string) => string): string | null {
  const where = issue.where;
  if (!where) return null;
  if (where.serverId) return labelOf(where.serverId);
  if (where.machineId) return `Machine ${where.machineId}`;
  if (where.busId) return where.busId;
  if (where.pipelineId != null) return String(where.pipelineId);
  if (where.entity) return where.entity;
  return null;
}
