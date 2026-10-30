import { colors } from '../theme/colors';
import { useConnectionStore } from '../stores/connectionStore';
import { countColor } from '../constants/queueThresholds';
import type {
  AlertMessage,
  BusEntry,
  ConfigIssue,
  ConnectionState,
  EdgeLevel,
  HealthState,
  MonitoredObject,
  NodeStatus,
  ObjectStatus,
  Pair,
  Pipeline,
  PlatformDef,
  RelaySummary,
  SummaryLevel,
  TopologyEdge,
  TopologyNode,
} from '../types/relay';
import { formatAgeSeconds } from './format';

export interface MetricsStaleness {
  ageSeconds: number | null;
  asOf: string | null;
}

interface StaleFields {
  metricsStale?: boolean;
  metricsAsOf?: string | null;
  metricsAgeSeconds?: number | null;
}

export function metricsStaleness(entry: StaleFields | null | undefined): MetricsStaleness | null {
  if (!entry || entry.metricsStale !== true) return null;
  const age = typeof entry.metricsAgeSeconds === 'number' && Number.isFinite(entry.metricsAgeSeconds)
    ? entry.metricsAgeSeconds
    : null;
  return { ageSeconds: age, asOf: entry.metricsAsOf ?? null };
}

export function staleMetricsNote(info: MetricsStaleness | null | undefined, capitalise = false): string {
  if (!info) return '';
  const text = info.ageSeconds == null
    ? 'counts are a last known reading'
    : `counts last read ${formatAgeSeconds(info.ageSeconds)} ago`;
  return capitalise ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

function worseStaleness(a: MetricsStaleness | null, b: MetricsStaleness | null): MetricsStaleness | null {
  if (!a) return b;
  if (!b) return a;
  if (a.ageSeconds == null) return a;
  if (b.ageSeconds == null) return b;
  return b.ageSeconds > a.ageSeconds ? b : a;
}

export function platformMetricsStaleness(
  node: TopologyNode | undefined | null,
  platformPipelines: Pipeline[]
): MetricsStaleness | null {
  const own = metricsStaleness(node);
  if (own) return own;
  if (platformPipelines.length === 0) return null;
  let worst: MetricsStaleness | null = null;
  for (const p of platformPipelines) {
    const info = metricsStaleness(p);
    if (!info) return null;
    worst = worseStaleness(worst, info);
  }
  return worst;
}

export function pipelinesForPlatform(
  platformId: string,
  node: TopologyNode | undefined | null,
  pipelines: Record<string, Pipeline>
): Pipeline[] {
  const listed = (node?.pipelines ?? []).map((id) => pipelines[String(id)]).filter(Boolean) as Pipeline[];
  if (listed.length > 0) return listed;
  return Object.values(pipelines).filter((p) => String(p.pipelineId).startsWith(`${platformId}:`));
}

const PIPELINES_WAITING_RE = /\b\d+\s+pipelines?\s+waiting\b/i;

export function pipelinesWaitingClause(count: number | null | undefined): string {
  if (count == null || !Number.isFinite(count) || count <= 0) return '';
  return ` -- ${count} pipeline${count === 1 ? '' : 's'} waiting`;
}

export function withPipelinesWaiting(text: string, count: number | null | undefined): string {
  if (!text) return text;
  const clause = pipelinesWaitingClause(count);
  if (!clause) return text;
  if (PIPELINES_WAITING_RE.test(text)) return text;
  const trimmed = text.replace(/\s+$/, '');
  return trimmed.endsWith('.') ? `${trimmed.slice(0, -1)}${clause}.` : `${trimmed}${clause}`;
}

export function alertPipelinesWaiting(
  alert: Pick<AlertMessage, 'affects' | 'affectsPipelineCount'> | null | undefined
): number | null {
  const published = alert?.affectsPipelineCount;
  if (typeof published === 'number' && published > 0) return published;
  const ids = alertBlockedPipelineIds(alert);
  return ids.length > 0 ? ids.length : null;
}

function alertBlockedPipelineIds(alert: Pick<AlertMessage, 'affects'> | null | undefined): string[] {
  const list = alert?.affects;
  if (!Array.isArray(list)) return [];
  return list.map(String).filter((a) => a.startsWith('pipeline:'));
}

function nodePublishedWaiting(node: TopologyNode | undefined | null): { ids: string[] | null; count: number | null } {
  if (!node) return { ids: null, count: null };
  if (Array.isArray(node.affects) && node.affects.length > 0) {
    return { ids: node.affects.map(String), count: node.affects.length };
  }
  const published = node.affectsPipelineCount ?? node.blockedPipelines;
  if (typeof published === 'number' && published > 0) {
    return { ids: null, count: published };
  }
  return { ids: null, count: null };
}

export function platformPipelinesWaiting(
  platformId: string,
  node: TopologyNode | undefined,
  topologyNodes: Record<string, TopologyNode>,
  activeAlerts: Record<string, AlertMessage>
): number | null {
  return platformBlockedPipelines(platformId, node, topologyNodes, activeAlerts).count;
}

function platformBlockedPipelines(
  platformId: string,
  node: TopologyNode | undefined,
  topologyNodes: Record<string, TopologyNode>,
  activeAlerts: Record<string, AlertMessage>
): { ids: Set<string>; count: number | null } {
  const own = nodePublishedWaiting(node);
  if (own.ids != null) return { ids: new Set(own.ids), count: own.ids.length };
  if (own.count != null) return { ids: new Set<string>(), count: own.count };

  const union = new Set<string>();
  let maxCount = 0;

  for (const alert of Object.values(activeAlerts)) {
    if (alert.event === 'resolved' || alert.resolvedAt) continue;
    if (!isAlertAboutPlatform(alert, platformId)) continue;
    const ids = alertBlockedPipelineIds(alert);
    for (const id of ids) union.add(id);
    if (ids.length === 0) maxCount = Math.max(maxCount, alert.affectsPipelineCount ?? 0);
  }

  for (const end of ['sender', 'receiver'] as const) {
    const fromEnd = nodePublishedWaiting(topologyNodes[`relay:${platformId}:${end}`]);
    for (const id of fromEnd.ids ?? []) union.add(id);
    if (fromEnd.ids == null && fromEnd.count != null) maxCount = Math.max(maxCount, fromEnd.count);
  }

  if (union.size > 0) return { ids: union, count: union.size };
  return { ids: union, count: maxCount > 0 ? maxCount : null };
}

export function platformsPipelinesWaiting(
  platformIds: string[],
  topologyNodes: Record<string, TopologyNode>,
  activeAlerts: Record<string, AlertMessage>
): number | null {
  if (platformIds.length === 0) return null;
  if (platformIds.length === 1) {
    return platformPipelinesWaiting(platformIds[0], topologyNodes[platformIds[0]], topologyNodes, activeAlerts);
  }
  const union = new Set<string>();
  for (const id of platformIds) {
    const blocked = platformBlockedPipelines(id, topologyNodes[id], topologyNodes, activeAlerts);
    if (blocked.ids.size === 0) return null;
    for (const pipelineId of blocked.ids) union.add(pipelineId);
  }
  return union.size > 0 ? union.size : null;
}

export function platformHeartbeatAge(
  node: TopologyNode | undefined | null,
  end: 'sender' | 'receiver',
  relayNode?: TopologyNode | undefined | null
): number | null {
  if (!node) return null;
  if (node.heartbeatReadable === false || relayNode?.heartbeatReadable === false) return null;
  const age = end === 'sender' ? node.senderHeartbeatAge : node.receiverHeartbeatAge;
  return age == null || !Number.isFinite(age) ? null : age;
}

export function isAlertAboutPlatform(alert: Pick<AlertMessage, 'rootCause'>, platformId: string): boolean {
  const rc = alert.rootCause ?? '';
  return rc.startsWith(`relay:${platformId}:`) || rc.startsWith(`db:${platformId}:`);
}

export function summaryPlatformId(
  summary: Pick<RelaySummary, 'headline' | 'reason'>,
  platforms: Record<string, PlatformDef>
): string | null {
  const ids = summaryPlatformIds(summary, platforms);
  return ids.length === 1 ? ids[0] : null;
}

export function summaryPlatformIds(
  summary: Pick<RelaySummary, 'headline' | 'reason'>,
  platforms: Record<string, PlatformDef>
): string[] {
  for (const text of [summary.reason, summary.headline]) {
    const ids = new Set(
      [...(text ?? '').matchAll(/\b(?:relay|db):([\w-]+):(?:sender|receiver|tcp|auth|query)\b/g)]
        .map((m) => m[1])
        .filter((id) => !!platforms[id])
    );
    if (ids.size > 0) return [...ids];
  }

  const headline = (summary.headline ?? '').toLowerCase();
  if (!/\b(sender|receiver|database|cannot be checked)\b/.test(headline)) return [];
  let mentioned = Object.values(platforms).filter((p) => {
    const label = (p.label ?? '').toLowerCase();
    return !!label && headline.includes(label);
  });
  mentioned = mentioned.filter(
    (p) =>
      !mentioned.some(
        (other) =>
          other.id !== p.id &&
          (other.label ?? '').toLowerCase().includes((p.label ?? '').toLowerCase()) &&
          (other.label ?? '').length > (p.label ?? '').length
      )
  );
  if (mentioned.length === 0) return [];
  if (!mentioned.some((p) => headline.startsWith((p.label ?? '').toLowerCase()))) return [];
  return mentioned.map((p) => p.id);
}

export function applyBlockedCountToSummary(
  status: OverallStatus,
  summary: Pick<RelaySummary, 'headline' | 'reason'> | null | undefined,
  platforms: Record<string, PlatformDef>,
  topologyNodes: Record<string, TopologyNode>,
  activeAlerts: Record<string, AlertMessage>
): OverallStatus {
  if (!summary) return status;
  const platformIds = summaryPlatformIds(summary, platforms);
  if (platformIds.length === 0) return status;
  const count = platformsPipelinesWaiting(platformIds, topologyNodes, activeAlerts);
  const title = withPipelinesWaiting(status.title, count);
  return title === status.title ? status : { ...status, title };
}

export function isRelayOrDatabaseAlert(alert: Pick<AlertMessage, 'rootCause'>): boolean {
  const rc = alert.rootCause ?? '';
  return /^relay:[\w-]+:(sender|receiver)$/.test(rc) || /^db:[\w-]+:(tcp|auth|query)$/.test(rc);
}

export function alertAgeIso(
  alert: Pick<AlertMessage, 'ts' | 'since' | 'event' | 'resolvedAt'>
): string | null {
  const isResolved = alert.event === 'resolved' || !!alert.resolvedAt;
  if (isResolved) return alert.resolvedAt ?? alert.ts ?? null;
  return alert.since ?? null;
}

export function isQueuedUnknown(entry: Pick<Pair, 'queued' | 'queuedUnknown'> | Pick<TopologyEdge, 'queued' | 'queuedUnknown'> | null | undefined): boolean {
  if (!entry) return false;
  return entry.queuedUnknown === true || entry.queued == null;
}

export function healthColor(health: HealthState): string {
  switch (health) {
    case 'Healthy':
      return colors.good;
    case 'Degraded':
      return colors.warn;
    case 'Down':
      return colors.unknown;
    case 'Blocked':
      return colors.blocked;
    case 'Disabled':
      return colors.textMuted;
    case 'Unknown':
    default:
      return colors.unknown;
  }
}

export function pipelineHealthColor(p: Pick<Pipeline, 'health' | 'pendingOutbox' | 'pendingInbox' | 'queuedCount'>): string {
  if (p.health !== 'Degraded') return healthColor(p.health);
  const worstPending = Math.max(p.pendingOutbox ?? 0, p.pendingInbox ?? 0, p.queuedCount ?? 0);
  return countColor(worstPending, colors.warn);
}

export function edgeColor(level: EdgeLevel): string {
  switch (level) {
    case 'green':
      return colors.good;
    case 'yellow':
      return colors.warn;
    case 'red':
      return colors.bad;
    case 'grey':
    default:
      return colors.unknown;
  }
}

export function nodeColor(status: NodeStatus): string {
  return status === 'ok' ? colors.good : colors.unknown;
}

export function objectColor(status: ObjectStatus): string {
  switch (status) {
    case 'ok':
      return colors.good;
    case 'down':
      return colors.unknown;
    case 'blocked':
      return colors.blocked;
    case 'unknown':
    default:
      return colors.unknown;
  }
}

export function objectStatusWord(status: ObjectStatus): string {
  switch (status) {
    case 'ok':
      return 'OK';
    case 'down':
      return 'Down';
    case 'blocked':
      return 'Blocked';
    case 'unknown':
    default:
      return 'checking…';
  }
}

function platformLabel(id: string): string {
  return useConnectionStore.getState().platforms[id]?.label ?? id;
}

function platformFromObjectId(id: string): string | null {
  const m = id.match(/^db:([\w-]+):/);
  return m ? platformLabel(m[1]) : null;
}

function looksLikeMachineId(s: string): boolean {
  const lower = s.toLowerCase();
  if (/emulator|docker|compose/.test(lower)) return true;
  if (!/\s/.test(s.trim())) return true;
  return false;
}

function isHumanLabel(s: string | null | undefined): s is string {
  return !!s && !looksLikeMachineId(s);
}

function deriveBusLabelFromId(id: string | null | undefined): string | null {
  if (!id) return null;
  const m = id.match(/(\d+)\s*$/);
  if (m) return `Service Bus ${m[1]}`;
  return null;
}

export function busDisplayName(
  bus: Pick<BusEntry, 'id' | 'name'> & Partial<Pick<BusEntry, 'label'>> | null | undefined,
  fallbackId?: string | null
): string {
  if (bus) {
    if (isHumanLabel(bus.label)) return bus.label;
    if (isHumanLabel(bus.name)) return bus.name;
    return deriveBusLabelFromId(bus.id) ?? bus.name ?? bus.id;
  }
  return deriveBusLabelFromId(fallbackId) ?? fallbackId ?? 'Service Bus';
}

export function objectLabelFor(obj: MonitoredObject, buses: Record<string, BusEntry>): string {
  if (isHumanLabel(obj.label)) return obj.label;

  if (obj.id === 'emulator:health') return 'Service Bus';
  if (obj.id === 'amqp:endpoint') return 'Bus Connection';

  const busMatch = obj.id.match(/^bus:([\w-]+):(health|amqp)$/);
  if (busMatch) {
    const name = busDisplayName(buses[busMatch[1]], busMatch[1]);
    return `${name} ${busMatch[2] === 'health' ? '– health endpoint' : '– connection'}`;
  }

  const dbMatch = obj.id.match(/^db:([\w-]+):(tcp|auth|query)$/);
  if (dbMatch) {
    const suffix = dbMatch[2] === 'tcp' ? '' : dbMatch[2] === 'auth' ? ' Auth' : ' Query';
    return `${platformLabel(dbMatch[1])}${suffix}`;
  }

  return obj.id;
}

export function plainReasonForObject(obj: MonitoredObject): string {
  const platform = platformFromObjectId(obj.id);
  if (obj.id === 'emulator:health' || obj.id === 'amqp:endpoint') return 'Service Bus is not answering.';
  const busMatch = obj.id.match(/^bus:([\w-]+):(health|amqp)$/);
  if (busMatch) {
    const name = busDisplayName(useConnectionStore.getState().buses[busMatch[1]], busMatch[1]);
    return `${name} is not answering.`;
  }
  if (platform && obj.id.endsWith(':tcp')) return `${platform} server is not answering.`;
  if (platform && obj.id.endsWith(':auth')) return `${platform} server rejected the connection.`;
  if (platform && obj.id.endsWith(':query')) return `${platform} server is not responding to queries.`;
  return `${objectLabelFor(obj, useConnectionStore.getState().buses)} is having a problem.`;
}

function platformEndFromPipelineName(name: string): { platform: string; end: 'sender' | 'receiver' } | null {
  const m = name.match(/^([\w-]+)-(in|out)$/);
  if (!m) return null;
  return { platform: m[1], end: m[2] === 'in' ? 'receiver' : 'sender' };
}

export function humanizeRawReason(raw: string | null | undefined): string | null {
  if (!raw) return null;

  if (raw === 'service_bus' || raw === 'emulator:health' || raw === 'amqp:endpoint') {
    return 'Service Bus is not answering.';
  }

  const busMatch = raw.match(/^bus:([\w-]+):(health|amqp)$/);
  if (busMatch) {
    const name = busDisplayName(useConnectionStore.getState().buses[busMatch[1]], busMatch[1]);
    return `${name} is not answering.`;
  }

  const relayMatch = raw.match(/^relay:([\w-]+):(sender|receiver)$/);
  if (relayMatch) {
    return `${platformLabel(relayMatch[1])} ${relayMatch[2]} relay has stopped responding.`;
  }

  const dbMatch = raw.match(/^db:([\w-]+):(tcp|auth|query)$/);
  if (dbMatch) {
    return `${platformLabel(dbMatch[1])} database is not reachable.`;
  }

  const busDetailMatch = raw.match(/^([\w-]+):\s*messages sent but not received\s*\(bus\)$/i);
  if (busDetailMatch) {
    const pe = platformEndFromPipelineName(busDetailMatch[1]);
    if (pe) {
      return `Messages for the ${platformLabel(pe.platform)} ${pe.end} are stuck in the Service Bus.`;
    }
    return 'Messages are stuck in the Service Bus.';
  }

  return null;
}

export function plainNodeDetail(
  label: string,
  detail: string | null | undefined,
  end?: 'sender' | 'receiver' | null,
  heartbeatReadable?: boolean
): string {
  const norm = (detail ?? '').replace(/\s*\(mock\)$/i, '').trim().toLowerCase();

  if (heartbeatReadable === false || norm.startsWith('could not read')) {
    return end
      ? `${label} ${end} cannot be checked right now -- its database did not answer.`
      : `${label} cannot be checked right now -- its database did not answer.`;
  }

  // Matches both current and older relay heartbeat wording.
  if (norm === 'stale or unreadable heartbeat' || norm.startsWith('heartbeat is stale')) {
    return end ? `${label} ${end} is not running.` : `${label} relay is not running.`;
  }
  if (norm === 'db chain down' || norm.startsWith('db chain down')) {
    return `${label} database is not reachable.`;
  }
  return `${label} is not responding.`;
}

export function sanitizeBusNamesInText(text: string): string {
  if (!text) return text;
  const buses = useConnectionStore.getState().buses;
  let out = text;
  for (const bus of Object.values(buses)) {
    if (!bus.name || !looksLikeMachineId(bus.name)) continue;
    const friendly = busDisplayName(bus);
    const escaped = bus.name.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    out = out.replace(new RegExp(`"${escaped}"\\s*\\(${bus.id ?? ''}\\)`, 'g'), `"${friendly}"`);
    out = out.replace(new RegExp(escaped, 'g'), friendly);
  }
  return out;
}

export function pipelineDisplayName(
  p: Pipeline,
  platforms: Record<string, PlatformDef>
): { display: string; raw: string | null } {
  if (p.displayName && p.displayName.trim()) return { display: p.displayName, raw: p.name };
  const sourceLabel = platforms[p.sourceSystem]?.label;
  const targetLabel = platforms[p.targetSystem]?.label;
  if (sourceLabel && targetLabel) return { display: `${sourceLabel} → ${targetLabel}`, raw: p.name };
  return { display: p.name, raw: null };
}

export function plainReasonForPipeline(p: Pipeline): string {
  if (p.health === 'Down') {
    const age = p.heartbeatAgeSeconds;
    if (age != null && age > 120) {
      return `${p.name}: heartbeat hasn't been seen in ${formatAgeSeconds(age)} -- its database may still be up, this pipeline just hasn't checked in.`;
    }
    return `${p.name}: this relay has stopped responding.`;
  }
  if (p.health === 'Blocked') {
    const blocked = (p.blockedBy && humanizeRawReason(p.blockedBy)) || 'waiting on an upstream problem.';
    return `${p.name}: ${blocked}`;
  }
  if (p.health === 'Degraded') {
    if (p.failedOutbox > 0 || p.unresolvedInbox > 0) {
      return `${p.name}: some messages failed to process.`;
    }
    return `${p.name}: messages are waiting to be sent.`;
  }
  return `${p.name}: needs attention.`;
}

export interface OverallStatus {
  level: 'good' | 'attention' | 'problem' | 'offline' | 'outage';
  title: string;
  reason: string;
  color: string;
  since?: string | null;
}

const SUMMARY_LEVEL_STYLE: Record<SummaryLevel, { color: string; title: string }> = {
  good: { color: colors.good, title: 'ALL GOOD' },
  attention: { color: colors.warn, title: 'ATTENTION' },
  problem: { color: colors.bad, title: 'PROBLEM' },
  offline: { color: colors.unknown, title: 'NO CONNECTION' },
  outage: { color: colors.unknown, title: 'OUTAGE' },
};

export function statusFromSummary(summary: RelaySummary): OverallStatus {
  const style = SUMMARY_LEVEL_STYLE[summary.level] ?? SUMMARY_LEVEL_STYLE.good;
  return {
    level: summary.level,
    title: sanitizeBusNamesInText(summary.headline || style.title),
    reason: sanitizeBusNamesInText(humanizeRawReason(summary.reason) ?? summary.reason),
    color: style.color,
    since: summary.since ?? null,
  };
}

export { aggregateMachineSummaries } from './relaySummary';

export function applyConfigIssuesOverride(status: OverallStatus, configIssues: ConfigIssue[]): OverallStatus {
  if (status.level !== 'good') return status;
  const errorIssue = configIssues.find((i) => i.severity === 'error');
  if (!errorIssue) return status;
  return {
    level: 'attention',
    title: 'ATTENTION',
    reason: `Configuration problem: ${errorIssue.message}`,
    color: colors.warn,
  };
}

export function deriveOverallStatus(
  connectionState: ConnectionState,
  pipelines: Pipeline[],
  objects: MonitoredObject[],
  edges: TopologyEdge[]
): OverallStatus {
  if (connectionState !== 'live') {
    return {
      level: 'offline',
      title: 'NO CONNECTION',
      reason: 'Not connected to the monitoring relay.',
      color: colors.unknown,
    };
  }

  const redEdge = edges.find((e) => e.level === 'red');
  if (redEdge) {
    return {
      level: 'problem',
      title: 'PROBLEM',
      reason: 'Messages are piling up and not moving through Service Bus.',
      color: colors.bad,
    };
  }

  const degradedPipeline = pipelines.find((p) => p.health === 'Degraded');
  const yellowEdge = edges.find((e) => e.level === 'yellow');
  if (degradedPipeline || yellowEdge) {
    return {
      level: 'attention',
      title: 'ATTENTION',
      reason: degradedPipeline ? plainReasonForPipeline(degradedPipeline) : 'Messages are waiting at the bus.',
      color: colors.warn,
    };
  }

  const downObject = objects.find((o) => o.status === 'down');
  const downPipeline = pipelines.find((p) => p.health === 'Down');
  const blockedObject = objects.find((o) => o.status === 'blocked');
  const blockedPipeline = pipelines.find((p) => p.health === 'Blocked');
  const greyEdge = edges.find((e) => e.level === 'grey');

  if (downObject || downPipeline || blockedObject || blockedPipeline || greyEdge) {
    const reason = downObject
      ? plainReasonForObject(downObject)
      : downPipeline
        ? plainReasonForPipeline(downPipeline)
        : blockedObject
          ? plainReasonForObject(blockedObject)
          : blockedPipeline
            ? plainReasonForPipeline(blockedPipeline)
            : 'One or more relays are not responding.';
    return { level: 'attention', title: 'ATTENTION', reason, color: colors.unknown };
  }

  return { level: 'good', title: 'ALL GOOD', reason: 'Everything is flowing normally.', color: colors.good };
}

export function applyPureOutageFloor(status: OverallStatus, edges: TopologyEdge[], objects: MonitoredObject[] = []): OverallStatus {
  if (status.level !== 'problem' && status.level !== 'attention') return status;
  const hasBacklog = edges.some((e) => e.level === 'red' || e.level === 'yellow');
  if (hasBacklog) return status;
  const hasDownDatabase = objects.some((o) => o.status === 'down' && String(o.kind || '').startsWith('db_'));
  if (hasDownDatabase) return status;
  return { ...status, color: colors.unknown };
}
