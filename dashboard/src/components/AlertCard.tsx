import React, { useEffect, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { createThemedStyles } from '../theme/ThemeContext';
import { colors } from '../theme/colors';
import { formatTimeAgo } from '../utils/format';
import {
  alertAgeIso,
  alertPipelinesWaiting,
  isRelayOrDatabaseAlert,
  sanitizeBusNamesInText,
  withPipelinesWaiting,
} from '../utils/health';
import { useConnectionStore } from '../stores/connectionStore';
import { useUIStore } from '../stores/uiStore';
import { PinPromptModal } from './PinPromptModal';
import { PushMuteButton } from './PushMuteButton';
import type { AlertMessage } from '../types/relay';
import { ACK_NOTE, ackFailureText, ackedByText } from '../utils/ackFeedback';

const useStyles = createThemedStyles((c) => ({
  card: {
    backgroundColor: c.surface,
    borderRadius: 12,
    padding: 16,
    borderLeftWidth: 5,
    gap: 8,
  },
  topRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start' },
  severity: { fontSize: 12, fontWeight: '800', color: c.textMuted },
  time: { fontSize: 12, color: c.textMuted },
  reason: { fontSize: 15, color: c.textPrimary, fontWeight: '600' },
  countsRow: { flexDirection: 'row', gap: 16 },
  countLabel: { fontSize: 12, color: c.textSecondary },
  expandBtn: { alignSelf: 'flex-start' },
  expandText: { fontSize: 12, color: c.textMuted, textDecorationLine: 'underline' },
  detailBox: { backgroundColor: c.surfaceRaised, borderRadius: 8, padding: 10, gap: 6 },
  detailText: { fontSize: 12, color: c.textSecondary },
  btnRow: { flexDirection: 'row', gap: 10, marginTop: 4 },
  btn: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: 8 },
  btnText: { fontWeight: '700', fontSize: 13 },
  ackedPill: {
    alignSelf: 'flex-start',
    backgroundColor: c.goodDim,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
  resolvedPill: {
    alignSelf: 'flex-start',
    backgroundColor: c.surfaceRaised,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 4,
  },
}));

function severityColor(sev: number) {
  if (sev <= 1) return colors.bad;
  if (sev === 2) return colors.bad;
  if (sev === 3) return colors.warn;
  return colors.textMuted;
}

interface Props {
  alert: AlertMessage;
  onSilence: (alertId: string) => void;
  onAck: (alertId: string, pin: string) => void;
  count?: number;
  alertIds?: string[];
  highlight?: boolean;
}

export function AlertCard({ alert, onSilence, onAck, count = 1, alertIds, highlight }: Props) {
  const styles = useStyles();
  const displayCount = alert.failedCount ?? count;
  const reasonText = isRelayOrDatabaseAlert(alert)
    ? withPipelinesWaiting(sanitizeBusNamesInText(alert.reason), alertPipelinesWaiting(alert))
    : sanitizeBusNamesInText(alert.reason);
  const ageIso = alertAgeIso(alert);
  const [pinVisible, setPinVisible] = useState(false);
  const [expanded, setExpanded] = useState(false);
  const silencedUntil = useConnectionStore((s) => s.silencedAlertIds[alert.alertId]);
  const ackError = useConnectionStore((s) => s.ackErrors[alert.alertId]);
  const clearAckError = useConnectionStore((s) => s.clearAckError);
  const showError = useUIStore((s) => s.showError);
  const silencedNow = silencedUntil ? silencedUntil > Date.now() : false;
  const isResolved = alert.event === 'resolved' || !!alert.resolvedAt;
  const color = isResolved ? colors.textMuted : severityColor(alert.severity);

  useEffect(() => {
    if (alert.acknowledged) setPinVisible(false);
  }, [alert.acknowledged]);

  useEffect(() => {
    if (!ackError) return;
    const text = ackFailureText(ackError);
    if (text) {
      setPinVisible(false);
      showError(text);
      clearAckError(alert.alertId);
    }
  }, [ackError, alert.alertId, clearAckError, showError]);

  return (
    <View style={[styles.card, { borderLeftColor: color }, highlight ? { borderWidth: 2, borderColor: colors.warn } : null]}>
      <View style={styles.topRow}>
        <Text style={[styles.severity, { color }]}>
          SEVERITY {alert.severity}
          {alert.escalated ? ' · ESCALATED' : ''}
        </Text>
        {ageIso ? <Text style={styles.time}>{formatTimeAgo(ageIso)}</Text> : null}
      </View>
      <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
        <Text style={[styles.reason, { flexShrink: 1 }]}>{reasonText}</Text>
        {displayCount > 1 ? (
          <View style={{ backgroundColor: colors.badDim, borderRadius: 999, paddingHorizontal: 8, paddingVertical: 2 }}>
            <Text style={{ color, fontWeight: '900', fontSize: 12 }}>×{displayCount}</Text>
          </View>
        ) : null}
      </View>
      <View style={styles.countsRow}>
        {alert.queuedCount > 0 ? <Text style={styles.countLabel}>Queued: {alert.queuedCount}</Text> : null}
        {alert.lostCount > 0 ? <Text style={styles.countLabel}>Lost: {alert.lostCount}</Text> : null}
      </View>

      <Pressable style={styles.expandBtn} onPress={() => setExpanded((v) => !v)}>
        <Text style={styles.expandText}>{expanded ? 'Hide details' : 'Show details'}</Text>
      </Pressable>
      {expanded ? (
        <View style={styles.detailBox}>
          {alert.detail ? <Text style={styles.detailText}>{alert.detail}</Text> : null}
          <Text style={styles.detailText}>Channels: {alert.channels.join(', ') || 'list only'}</Text>
        </View>
      ) : null}

      {isResolved ? (
        <View style={styles.resolvedPill}>
          <Text style={{ color: colors.textSecondary, fontSize: 12, fontWeight: '700' }}>Resolved</Text>
        </View>
      ) : alert.acknowledged ? (
        <View style={styles.ackedPill}>
          <Text style={{ color: colors.good, fontSize: 12, fontWeight: '700' }}>
            {ackedByText(alert.acknowledged_by, alert.acknowledged_at)}
          </Text>
        </View>
      ) : (
        <View style={styles.btnRow}>
          <Pressable
            style={[styles.btn, { backgroundColor: silencedNow ? colors.surfaceRaised : colors.warnDim }]}
            onPress={() => onSilence(alert.alertId)}
          >
            <Text style={[styles.btnText, { color: colors.warn }]}>
              {silencedNow ? 'Silenced 15m' : 'Silence 15 min'}
            </Text>
          </Pressable>
          <Pressable style={[styles.btn, { backgroundColor: colors.goodDim }]} onPress={() => setPinVisible(true)}>
            <Text style={[styles.btnText, { color: colors.good }]}>Acknowledge</Text>
          </Pressable>
        </View>
      )}

      {isResolved ? null : (
        <View style={styles.btnRow}>
          <PushMuteButton alertId={alert.alertId} alertIds={alertIds} />
        </View>
      )}

      <PinPromptModal
        visible={pinVisible}
        subtitle={`Acknowledging marks this alert acknowledged for every account. ${ACK_NOTE}`}
        errorText={ackError === 'invalid_pin' ? ackError : null}
        onCancel={() => {
          setPinVisible(false);
          clearAckError(alert.alertId);
        }}
        onSubmit={(pin) => onAck(alert.alertId, pin)}
      />
    </View>
  );
}
