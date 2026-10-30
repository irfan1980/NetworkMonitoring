import React, { useState } from 'react';
import { Modal, Pressable, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { createThemedStyles } from '../theme/ThemeContext';
import { colors } from '../theme/colors';
import { clearScenarios, triggerScenario, type ScenarioType } from '../services/relay.service';
import { useUIStore } from '../stores/uiStore';

const SCENARIOS: { type: ScenarioType; label: string }[] = [
  { type: 'receiver_missing', label: 'Receiver missing' },
  { type: 'dead_letter_spike', label: 'Dead-letter spike' },
  { type: 'sender_flood', label: 'Sender flood' },
  { type: 'slow_consumer', label: 'Slow consumer' },
  { type: 'all_healthy', label: 'All healthy' },
  { type: 'full_outage', label: 'Full outage' },
];

const useStyles = createThemedStyles((c) => ({
  backdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.6)', justifyContent: 'flex-end' },
  sheet: { backgroundColor: c.surfaceRaised, borderTopLeftRadius: 20, borderTopRightRadius: 20, padding: 20, gap: 10 },
  title: { color: c.textPrimary, fontSize: 16, fontWeight: '800' },
  subtitle: { color: c.textMuted, fontSize: 12, marginBottom: 8 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  btn: { backgroundColor: c.surface, borderRadius: 10, paddingVertical: 12, paddingHorizontal: 14, flexGrow: 1, minWidth: 140 },
  btnText: { color: c.textPrimary, fontWeight: '700', fontSize: 13, textAlign: 'center' },
  clearBtn: { backgroundColor: colors.badDim, borderRadius: 10, paddingVertical: 12, marginTop: 6 },
  clearText: { color: colors.bad, fontWeight: '800', textAlign: 'center' },
  closeBtn: { marginTop: 12, alignItems: 'center' },
  closeText: { color: c.textMuted },
}));

export function DevPanel({ visible, onClose }: { visible: boolean; onClose: () => void }) {
  const styles = useStyles();
  const insets = useSafeAreaInsets();
  const showSuccess = useUIStore((s) => s.showSuccess);
  const showError = useUIStore((s) => s.showError);
  const [busy, setBusy] = useState<string | null>(null);

  async function fire(type: ScenarioType) {
    setBusy(type);
    try {
      await triggerScenario(type);
      showSuccess(`Scenario "${type}" triggered`);
    } catch {
      showError('Could not reach the relay control API');
    } finally {
      setBusy(null);
    }
  }

  async function clear() {
    setBusy('clear');
    try {
      await clearScenarios();
      showSuccess('Scenarios cleared');
    } catch {
      showError('Could not reach the relay control API');
    } finally {
      setBusy(null);
    }
  }

  return (
    <Modal visible={visible} transparent animationType="slide" onRequestClose={onClose}>
      <Pressable style={styles.backdrop} onPress={onClose}>
        <Pressable
          style={[
            styles.sheet,
            { paddingBottom: 20 + insets.bottom, paddingLeft: 20 + insets.left, paddingRight: 20 + insets.right },
          ]}
          onPress={(e) => e.stopPropagation()}
        >
          <Text style={styles.title}>Dev panel (mock mode)</Text>
          <Text style={styles.subtitle}>Trigger a scenario on the relay's mock source.</Text>
          <View style={styles.grid}>
            {SCENARIOS.map((s) => (
              <Pressable key={s.type} style={styles.btn} disabled={busy !== null} onPress={() => fire(s.type)}>
                <Text style={styles.btnText}>{busy === s.type ? '...' : s.label}</Text>
              </Pressable>
            ))}
          </View>
          <Pressable style={styles.clearBtn} disabled={busy !== null} onPress={clear}>
            <Text style={styles.clearText}>{busy === 'clear' ? '...' : 'Clear scenarios'}</Text>
          </Pressable>
          <Pressable style={styles.closeBtn} onPress={onClose}>
            <Text style={styles.closeText}>Close</Text>
          </Pressable>
        </Pressable>
      </Pressable>
    </Modal>
  );
}
