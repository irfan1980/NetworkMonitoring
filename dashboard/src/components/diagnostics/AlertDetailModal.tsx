import React from 'react';
import { Text, View } from 'react-native';
import { createThemedStyles } from '../../theme/ThemeContext';
import { useConnectionStore } from '../../stores/connectionStore';
import { formatTimeAgo } from '../../utils/format';
import {
  alertAgeIso,
  alertPipelinesWaiting,
  isRelayOrDatabaseAlert,
  sanitizeBusNamesInText,
  withPipelinesWaiting,
} from '../../utils/health';
import { DiagnosticsModal } from './DiagnosticsModal';

const useStyles = createThemedStyles((c) => ({
  reason: { color: c.textPrimary, fontSize: 18, fontWeight: '800' },
  meta: { color: c.textMuted, fontSize: 13, marginTop: 4 },
  detailBox: { backgroundColor: c.surface, borderRadius: 10, padding: 12, gap: 6, marginTop: 16 },
  detailText: { color: c.textSecondary, fontSize: 13 },
}));

interface Props {
  alertId: string | null;
  onClose: () => void;
}

export function AlertDetailModal({ alertId, onClose }: Props) {
  const styles = useStyles();
  const alert = useConnectionStore((s) =>
    alertId ? s.activeAlerts[alertId] ?? s.alertHistory.find((a) => a.alertId === alertId) : undefined
  );

  return (
    <DiagnosticsModal visible={!!alertId} title={alert ? `Severity ${alert.severity} alert` : 'Alert'} onClose={onClose}>
      {alert ? (
        <View>
          <Text style={styles.reason}>
            {isRelayOrDatabaseAlert(alert)
              ? withPipelinesWaiting(sanitizeBusNamesInText(alert.reason), alertPipelinesWaiting(alert))
              : sanitizeBusNamesInText(alert.reason)}
          </Text>
          {alertAgeIso(alert) || alert.escalated ? (
            <Text style={styles.meta}>
              {[alertAgeIso(alert) ? formatTimeAgo(alertAgeIso(alert)) : null, alert.escalated ? 'Escalated' : null]
                .filter(Boolean)
                .join(' · ')}
            </Text>
          ) : null}
          {alert.detail || (alert.channels?.length ?? 0) > 0 ? (
            <View style={styles.detailBox}>
              {alert.detail ? <Text style={styles.detailText}>{alert.detail}</Text> : null}
              <Text style={styles.detailText}>Channels: {(alert.channels ?? []).join(', ') || 'list only'}</Text>
            </View>
          ) : null}
        </View>
      ) : null}
    </DiagnosticsModal>
  );
}
