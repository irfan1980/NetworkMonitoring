import React, { useMemo, useState } from 'react';
import { FlatList, Pressable, Text, View } from 'react-native';
import { useLocalSearchParams } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { createThemedStyles } from '../src/theme/ThemeContext';
import { useConnectionStore } from '../src/stores/connectionStore';
import { AlertCard } from '../src/components/AlertCard';
import { groupAlerts, sortGroupsBySeverityThenRecency, type AlertGroup } from '../src/utils/alertGrouping';

const useStyles = createThemedStyles((c) => ({
  screen: { flex: 1, backgroundColor: c.background },
  content: { padding: 16, gap: 12 },
  heading: { color: c.textPrimary, fontSize: 20, fontWeight: '800' },
  empty: { color: c.textMuted, fontSize: 14, marginTop: 24, textAlign: 'center' },
  moreBtn: {
    alignSelf: 'flex-start',
    backgroundColor: c.surfaceRaised,
    borderRadius: 999,
    paddingHorizontal: 16,
    paddingVertical: 10,
  },
  moreText: { color: c.textPrimary, fontWeight: '800', fontSize: 13 },
}));

const VISIBLE_CAP = 25;

function hiddenAlertCount(groups: AlertGroup[], visibleCount: number): number {
  return groups.slice(visibleCount).reduce((sum, g) => sum + g.count, 0);
}

type Row =
  | { kind: 'heading'; key: string; text: string }
  | { kind: 'group'; key: string; group: AlertGroup }
  | { kind: 'more'; key: string; count: number; onPress: () => void }
  | { kind: 'empty'; key: string };

export default function AlertsScreen() {
  const styles = useStyles();
  const { alertId: focusAlertId } = useLocalSearchParams<{ alertId?: string }>();
  const insets = useSafeAreaInsets();
  const alertFeed = useConnectionStore((s) => s.alertFeed);
  const alertHistory = useConnectionStore((s) => s.alertHistory);
  const wsActions = useConnectionStore((s) => s.wsActions);
  const [activeExpanded, setActiveExpanded] = useState(false);
  const [historyExpanded, setHistoryExpanded] = useState(false);

  const activeGroups = useMemo(() => {
    const sorted = sortGroupsBySeverityThenRecency(groupAlerts(alertFeed));
    const at = focusAlertId ? sorted.findIndex((g) => g.alertIds.includes(focusAlertId)) : -1;
    return at > 0 ? [sorted[at], ...sorted.slice(0, at), ...sorted.slice(at + 1)] : sorted;
  }, [alertFeed, focusAlertId]);
  const historyGroups = useMemo(
    () => sortGroupsBySeverityThenRecency(groupAlerts(alertHistory)),
    [alertHistory]
  );

  const rows = useMemo<Row[]>(() => {
    const out: Row[] = [];
    out.push({ kind: 'heading', key: 'h-active', text: 'Alerts' });
    if (activeGroups.length === 0) {
      out.push({ kind: 'empty', key: 'e-active' });
    } else {
      const visible = activeExpanded ? activeGroups : activeGroups.slice(0, VISIBLE_CAP);
      for (const g of visible) out.push({ kind: 'group', key: `active:${g.alert.alertId}`, group: g });
      const remaining = hiddenAlertCount(activeGroups, visible.length);
      if (remaining > 0) {
        out.push({ kind: 'more', key: 'more-active', count: remaining, onPress: () => setActiveExpanded(true) });
      }
    }
    if (historyGroups.length > 0) {
      out.push({ kind: 'heading', key: 'h-history', text: 'Resolved (this session)' });
      const visible = historyExpanded ? historyGroups : historyGroups.slice(0, VISIBLE_CAP);
      for (const g of visible) out.push({ kind: 'group', key: `history:${g.alert.alertId}`, group: g });
      const remaining = hiddenAlertCount(historyGroups, visible.length);
      if (remaining > 0) {
        out.push({ kind: 'more', key: 'more-history', count: remaining, onPress: () => setHistoryExpanded(true) });
      }
    }
    return out;
  }, [activeGroups, historyGroups, activeExpanded, historyExpanded]);

  const applyToGroup = (fn: (id: string) => void) => (alertIds: string[]) => {
    for (const id of alertIds) fn(id);
  };
  const silenceGroup = applyToGroup((id) => wsActions?.sendSilence(id));
  const ackGroup = (alertIds: string[], pin: string) => {
    for (const id of alertIds) wsActions?.sendAck(id, pin);
  };

  return (
    <FlatList
      style={styles.screen}
      contentContainerStyle={[
        styles.content,
        { paddingLeft: 16 + insets.left, paddingRight: 16 + insets.right, paddingBottom: 16 + insets.bottom },
      ]}
      data={rows}
      keyExtractor={(r) => r.key}
      renderItem={({ item }) => {
        if (item.kind === 'heading') return <Text style={styles.heading}>{item.text}</Text>;
        if (item.kind === 'empty') return <Text style={styles.empty}>No active alerts.</Text>;
        if (item.kind === 'more') {
          return (
            <Pressable style={styles.moreBtn} onPress={item.onPress}>
              <Text style={styles.moreText}>+{item.count} more alert{item.count === 1 ? '' : 's'}</Text>
            </Pressable>
          );
        }
        const { group } = item;
        return (
          <AlertCard
            alert={group.alert}
            count={group.count}
            alertIds={group.alertIds}
            highlight={!!focusAlertId && group.alertIds.includes(focusAlertId)}
            onSilence={() => silenceGroup(group.alertIds)}
            onAck={(_id, pin) => ackGroup(group.alertIds, pin)}
          />
        );
      }}
      initialNumToRender={12}
      windowSize={7}
      removeClippedSubviews
    />
  );
}
