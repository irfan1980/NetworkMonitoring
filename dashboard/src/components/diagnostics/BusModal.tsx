import React, { useCallback, useMemo } from 'react';
import { Text, View } from 'react-native';
import { createThemedStyles } from '../../theme/ThemeContext';
import { colors } from '../../theme/colors';
import { fetchPairEntities } from '../../services/diagnostics';
import { useDiagnosticsPoll } from '../../hooks/useDiagnosticsPoll';
import { useConnectionStore } from '../../stores/connectionStore';
import { entityDotState } from '../../utils/diagnosticsStatus';
import { resolveIssues } from '../../utils/configIssues';
import { busDisplayName } from '../../utils/health';
import { wirePairTotals } from '../../utils/pairTotals';
import { formatTimeAgo } from '../../utils/format';
import { countColor } from '../../constants/queueThresholds';
import { DiagnosticsModal } from './DiagnosticsModal';
import { EntityDiagnosticRow } from './EntityDiagnosticRow';

const useStyles = createThemedStyles((c) => ({
  headline: { color: c.textPrimary, fontSize: 20, fontWeight: '800' },
  totalsRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 20, marginTop: 10, marginBottom: 16 },
  totalItem: { minWidth: 110 },
  totalLabel: { color: c.textMuted, fontSize: 11 },
  totalValue: { color: c.textPrimary, fontSize: 18, fontWeight: '800', marginTop: 2 },
  rows: { gap: 10 },
  centerBox: { paddingVertical: 40, alignItems: 'center', gap: 8 },
  centerText: { color: c.textSecondary, fontSize: 15 },
  errorText: { color: colors.bad, fontSize: 15, fontWeight: '700' },
  issueBox: { borderRadius: 10, padding: 12, marginBottom: 12, gap: 4 },
  issueText: { fontSize: 13, fontWeight: '700' },
  lostBox: { backgroundColor: c.surface, borderRadius: 10, borderLeftWidth: 4, padding: 12, gap: 6, marginBottom: 16 },
  lostTitle: { color: colors.bad, fontSize: 13, fontWeight: '800' },
  lostText: { color: c.textSecondary, fontSize: 13, lineHeight: 19 },
  unavailBox: { backgroundColor: colors.warnDim, borderRadius: 10, padding: 12, marginBottom: 12, gap: 4 },
  unavailText: { color: colors.warn, fontSize: 13, fontWeight: '700' },
  rowsNote: { color: c.textMuted, fontSize: 12, lineHeight: 18, marginTop: -8, marginBottom: 14 },
}));

interface Props {
  visible: boolean;
  from: string | null;
  to: string | null;
  fromLabel: string;
  toLabel: string;
  onClose: () => void;
}

export function BusModal({ visible, from, to, fromLabel, toLabel, onClose }: Props) {
  const styles = useStyles();
  const active = visible && !!from && !!to;
  const fetcher = useCallback(() => fetchPairEntities(from as string, to as string), [from, to]);
  const { data, loading, error } = useDiagnosticsPoll(fetcher, active, `${from}->${to}`);

  const pair = useConnectionStore((s) => (from && to ? s.pairs[`${from}->${to}`] : undefined));
  const configIssues = useConnectionStore((s) => s.configIssues);
  const buses = useConnectionStore((s) => s.buses);
  const pairIssues = useMemo(() => resolveIssues(pair?.issues, configIssues), [pair, configIssues]);

  const busLabel = useMemo(() => {
    if (!pair?.bus) return null;
    if (pair.busName && /\s/.test(pair.busName.trim())) return pair.busName;
    return busDisplayName(buses[pair.bus], pair.bus);
  }, [pair, buses]);

  const title = busLabel ? `${fromLabel} -> ${toLabel} · ${busLabel}` : `${fromLabel} -> ${toLabel}`;

  const topologyNodes = useConnectionStore((s) => s.topologyNodes);
  const pipelines = useConnectionStore((s) => s.pipelines);
  const wire = useMemo(() => wirePairTotals(pair, from, to, topologyNodes, pipelines), [pair, from, to, topologyNodes, pipelines]);
  const lost = wire.lost;

  const activity = useMemo(() => {
    const entities = data?.entities ?? [];
    return entities.reduce(
      (acc, e) => ({
        sent: acc.sent + e.sentInWindow,
        applied: acc.applied + e.appliedInWindow,
        errors: acc.errors + e.errors,
        unmatched: acc.unmatched + e.queued,
      }),
      { sent: 0, applied: 0, errors: 0, unmatched: 0 }
    );
  }, [data]);

  const fromDown = data?.sides?.from.available === false;
  const toDown = data?.sides?.to.available === false;
  const notes = useMemo(() => {
    const out: string[] = [];
    const side = data?.sides;
    if (side && !side.from.available) {
      out.push(`${fromLabel} ${side.from.reason === 'db_unreachable' ? 'is unreachable' : `is unavailable (${side.from.reason})`} - source-side data unavailable`);
    }
    if (side && !side.to.available) {
      out.push(`${toLabel} ${side.to.reason === 'db_unreachable' ? 'is unreachable' : `is unavailable (${side.to.reason})`} - target-side data unavailable`);
    }
    const bus = data?.bus;
    if (bus && !bus.available) {
      out.push(
        bus.reason === 'bus_down'
          ? `${busLabel ?? 'The Service Bus'} is down - bus-side data unavailable`
          : `${busLabel ?? 'The Service Bus'} status is unknown - bus-side data unavailable`
      );
    }
    return out;
  }, [data, fromLabel, toLabel, busLabel]);

  const rowsIncludeLost = lost > 0 && activity.unmatched > wire.queued;

  return (
    <DiagnosticsModal visible={visible} title={title} onClose={onClose}>
      {loading && !data ? (
        <View style={styles.centerBox}>
          <Text style={styles.centerText}>Loading what's flowing through this pipeline...</Text>
        </View>
      ) : error && !data ? (
        <View style={styles.centerBox}>
          <Text style={styles.errorText}>Could not load this pipeline's data. {error}</Text>
        </View>
      ) : (data?.entities?.length ?? 0) === 0 ? (
        <View style={styles.centerBox}>
          {notes.map((n) => (
            <Text key={n} style={styles.unavailText}>
              {n}
            </Text>
          ))}
          <Text style={styles.centerText}>
            {notes.length > 0 ? 'No rows could be read.' : `Nothing is configured to flow between ${fromLabel} and ${toLabel}.`}
          </Text>
        </View>
      ) : (
        <>
          {notes.map((n) => (
            <View key={n} style={styles.unavailBox}>
              <Text style={styles.unavailText}>{n}</Text>
            </View>
          ))}
          {pairIssues.length > 0
            ? pairIssues.map((issue) => (
                <View
                  key={issue.code}
                  style={[
                    styles.issueBox,
                    { backgroundColor: issue.severity === 'error' ? colors.badDim : colors.warnDim },
                  ]}
                >
                  <Text style={[styles.issueText, { color: issue.severity === 'error' ? colors.bad : colors.warn }]}>
                    {issue.message}
                  </Text>
                </View>
              ))
            : null}
          <Text style={styles.headline}>{data!.entities.length} {data!.entities.length === 1 ? 'kind' : 'kinds'} of data flowing</Text>
          <View style={styles.totalsRow}>
            <View style={styles.totalItem}>
              <Text style={styles.totalLabel}>Pending</Text>
              <Text style={[styles.totalValue, fromDown ? null : { color: countColor(wire.pending, colors.textPrimary) }]}>
                {fromDown ? 'unavailable' : wire.pending}
              </Text>
            </View>
            <View style={styles.totalItem}>
              <Text style={styles.totalLabel}>Queued</Text>
              <Text style={[styles.totalValue, wire.queuedUnknown ? null : { color: countColor(wire.queued, colors.textPrimary) }]}>
                {toDown ? 'unavailable' : wire.queuedUnknown ? '?' : wire.queued}
              </Text>
            </View>
            {wire.receiverBacklog > 0 ? (
              <View style={styles.totalItem}>
                <Text style={styles.totalLabel}>Waiting at {toLabel}</Text>
                <Text style={[styles.totalValue, { color: countColor(wire.receiverBacklog, colors.textPrimary) }]}>
                  {wire.receiverBacklog}
                </Text>
              </View>
            ) : null}
            {wire.failed > 0 ? (
              <View style={styles.totalItem}>
                <Text style={styles.totalLabel}>Failed</Text>
                <Text style={[styles.totalValue, { color: colors.bad }]}>
                  {'✕'} {wire.failed}
                </Text>
              </View>
            ) : null}
            {lost > 0 ? (
              <View style={styles.totalItem}>
                <Text style={styles.totalLabel}>Lost</Text>
                <Text style={[styles.totalValue, { color: colors.bad }]}>{lost}</Text>
              </View>
            ) : null}
            <View style={styles.totalItem}>
              <Text style={styles.totalLabel}>Sent last hour</Text>
              <Text style={styles.totalValue}>{fromDown ? 'unavailable' : activity.sent}</Text>
            </View>
            <View style={styles.totalItem}>
              <Text style={styles.totalLabel}>Applied last hour</Text>
              <Text style={styles.totalValue}>{toDown ? 'unavailable' : activity.applied}</Text>
            </View>
            <View style={styles.totalItem}>
              <Text style={styles.totalLabel}>Errors</Text>
              <Text style={[styles.totalValue, activity.errors > 0 && { color: colors.bad }]}>{activity.errors}</Text>
            </View>
          </View>
          {rowsIncludeLost ? (
            <Text style={styles.rowsNote}>
              Each count above is this string's own number from the map -- "Queued" ({wire.queued}) is still on its
              way, "Lost" ({lost}) is not, and no message is in both. The per-entity "Not matched yet" figures below
              are read straight from {toLabel} and still include the {lost} now counted as lost, so they add up to
              more.
            </Text>
          ) : null}
          {lost > 0 ? (
            <View style={[styles.lostBox, { borderLeftColor: colors.bad }]}>
              <Text style={styles.lostTitle}>What the red "lost {lost}" badge on this string means</Text>
              <Text style={styles.lostText}>
                {lost} message{lost === 1 ? '' : 's'} {fromLabel} sent to {toLabel}
                {busLabel ? ` over ${busLabel}` : ''} {lost === 1 ? 'has' : 'have'} not arrived, and nothing is
                moving on that route: {lost === 1 ? 'it has' : 'they have'} been outstanding for over an hour and
                nothing at all has arrived at {toLabel} for minutes.
                {pair?.lostSince ? ` First recorded ${formatTimeAgo(pair.lostSince)}.` : ''}
              </Text>
              <Text style={styles.lostText}>
                Late delivery still clears this by itself: the count falls as messages turn up, with nothing to
                acknowledge. Only a count that holds while the route stays quiet means those messages need
                re-sending from the source.
              </Text>
              <Text style={styles.lostText}>
                The count covers only messages going {fromLabel} {'->'} {toLabel} (the other direction is counted
                on its own string), and only ones past that age with the route quiet -- messages still on their way
                are in "Queued", and rows that never left the sender at all are the separate "✕ N failed" badge.
              </Text>
            </View>
          ) : null}
          <View style={styles.rows}>
            {data!.entities
              .slice()
              .sort((a, b) => {
                const rank = { red: 0, degraded: 1, yellow: 2, green: 3, grey: 4 } as const;
                return rank[entityDotState(a, data!.window.seconds)] - rank[entityDotState(b, data!.window.seconds)];
              })
              .map((entity, i) => (
                <EntityDiagnosticRow
                  key={`${i}-${entity.entity}`}
                  entity={entity}
                  windowSeconds={data!.window.seconds}
                  queuedIncludesLost={rowsIncludeLost}
                  fromDown={fromDown}
                  toDown={toDown}
                  fromLabel={fromLabel}
                  toLabel={toLabel}
                />
              ))}
          </View>
        </>
      )}
    </DiagnosticsModal>
  );
}
