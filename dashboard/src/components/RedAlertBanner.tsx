import React, { useEffect, useState } from 'react';
import { Pressable, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { createThemedStyles } from '../theme/ThemeContext';
import { colors } from '../theme/colors';
import { PushMuteButton } from './PushMuteButton';
import { useConnectionStore } from '../stores/connectionStore';
import { useUIStore } from '../stores/uiStore';
import { useResponsive } from '../hooks/useResponsive';
import {
  alertPipelinesWaiting,
  isRelayOrDatabaseAlert,
  sanitizeBusNamesInText,
  withPipelinesWaiting,
} from '../utils/health';
import { PinPromptModal } from './PinPromptModal';
import type { AlertMessage } from '../types/relay';
import { AppNav } from './AppNav';
import { ACK_NOTE, ackFailureText } from '../utils/ackFeedback';

const useStyles = createThemedStyles(() => ({
  bar: {
    backgroundColor: '#5a0000',
    paddingHorizontal: 16,
    paddingBottom: 10,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    flexWrap: 'wrap',
    gap: 8,
    flexGrow: 1,
  },
  text: { color: '#fff', fontWeight: '800', fontSize: 13, flexShrink: 1 },
  more: { color: '#ffb3b3', fontWeight: '700', fontSize: 12 },
  btnRow: { flexDirection: 'row', gap: 8, alignItems: 'center' },
  btn: { paddingHorizontal: 12, paddingVertical: 6, borderRadius: 8 },
  btnText: { fontWeight: '800', fontSize: 12 },
  silenced: { color: colors.warn, fontWeight: '800', fontSize: 11 },
}));

interface Props {
  alert: AlertMessage;
  otherActiveCount: number;
  onSilence: (alertId: string) => void;
  onAck: (alertId: string, pin: string) => void;
}

export function RedAlertBanner({ alert, otherActiveCount, onSilence, onAck }: Props) {
  const styles = useStyles();
  const insets = useSafeAreaInsets();
  const { isPhone } = useResponsive();
  const silencedAlertIds = useConnectionStore((s) => s.silencedAlertIds);
  const ackErrors = useConnectionStore((s) => s.ackErrors);
  const clearAckError = useConnectionStore((s) => s.clearAckError);
  const showError = useUIStore((s) => s.showError);
  const [pinVisible, setPinVisible] = useState(false);

  const ackError = ackErrors[alert.alertId];
  const silencedUntil = silencedAlertIds[alert.alertId];
  const silencedNow = silencedUntil ? silencedUntil > Date.now() : false;

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
		<View style={{ flexDirection: isPhone ? "column" : "row", borderColor: colors.bad, borderBottomWidth: 3 }}>
			<AppNav />
			<View style={[styles.bar, { paddingTop: isPhone ? 10 : insets.top + 10 }]}>
				<View style={{ flexShrink: 1, gap: 2 }}>
					<Text style={styles.text} numberOfLines={2}>
						{isRelayOrDatabaseAlert(alert)
							? withPipelinesWaiting(
									sanitizeBusNamesInText(alert.reason),
									alertPipelinesWaiting(alert),
								)
							: sanitizeBusNamesInText(alert.reason)}
					</Text>
					{otherActiveCount > 0 ? (
						<Text style={styles.more}>
							+{otherActiveCount} more active alert
							{otherActiveCount === 1 ? "" : "s"}
						</Text>
					) : null}
					{silencedNow ? (
						<Text style={styles.silenced}>
							Silenced for 15 min on this device
						</Text>
					) : null}
				</View>

				<View style={styles.btnRow}>
					<Pressable
						accessibilityRole="button"
						hitSlop={10}
						style={[styles.btn, { backgroundColor: colors.warn }]}
						onPress={() => onSilence(alert.alertId)}
					>
						<Text style={[styles.btnText, { color: "#3a0000" }]}>
							{silencedNow ? "Silenced" : "Silence 15 min"}
						</Text>
					</Pressable>
					<Pressable
						accessibilityRole="button"
						hitSlop={10}
						style={[styles.btn, { backgroundColor: "#fff" }]}
						onPress={() => setPinVisible(true)}
					>
						<Text style={[styles.btnText, { color: "#3a0000" }]}>
							Acknowledge (PIN)
						</Text>
					</Pressable>
					<PushMuteButton alertId={alert.alertId} compact />
				</View>
			</View>

			<PinPromptModal
				visible={pinVisible}
				title="Acknowledge"
				subtitle={`This acknowledges the alert for every account. ${ACK_NOTE}`}
				errorText={ackError === "invalid_pin" ? ackError : null}
				onCancel={() => {
					setPinVisible(false);
					clearAckError(alert.alertId);
				}}
				onSubmit={(pin) => onAck(alert.alertId, pin)}
			/>
		</View>
  );
}
