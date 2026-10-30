import React, { useEffect, useState } from 'react';
import { Modal, Pressable, Text, TextInput, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { createThemedStyles } from '../theme/ThemeContext';
import { colors } from '../theme/colors';

const useStyles = createThemedStyles((c) => ({
  backdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.6)', alignItems: 'center', justifyContent: 'center' },
  card: { backgroundColor: c.surfaceRaised, borderRadius: 16, padding: 24, width: 320, gap: 12 },
  title: { color: c.textPrimary, fontSize: 18, fontWeight: '800' },
  subtitle: { color: c.textSecondary, fontSize: 13 },
  input: {
    backgroundColor: c.background,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: c.border,
    color: c.textPrimary,
    fontSize: 20,
    letterSpacing: 6,
    textAlign: 'center',
    paddingVertical: 10,
  },
  inputError: { borderColor: colors.bad },
  errorText: { color: colors.bad, fontSize: 13, fontWeight: '700' },
  row: { flexDirection: 'row', gap: 10, marginTop: 8 },
  btn: { flex: 1, borderRadius: 8, paddingVertical: 10, alignItems: 'center' },
  btnPrimaryText: { color: '#0d1117', fontWeight: '800' },
  btnGhostText: { color: c.textSecondary, fontWeight: '700' },
}));

interface Props {
  visible: boolean;
  title?: string;
  subtitle?: string;
  errorText?: string | null;
  resetKey?: number;
  busy?: boolean;
  onCancel: () => void;
  onSubmit: (pin: string) => void;
}

export function PinPromptModal({
  visible,
  title = 'Acknowledge alert',
  subtitle,
  errorText,
  resetKey,
  busy,
  onCancel,
  onSubmit,
}: Props) {
  const styles = useStyles();
  const insets = useSafeAreaInsets();
  const [pin, setPin] = useState('');

  useEffect(() => {
    if (errorText) setPin('');
  }, [errorText, resetKey]);

  useEffect(() => {
    if (!visible) setPin('');
  }, [visible]);

  function submit() {
    if (busy || pin.trim().length === 0) return;
    onSubmit(pin.trim());
  }

  // On web the PIN input can sit inside a <form>; intercept Enter to avoid a native submit/reload.
  function handleKeyPress(e: { nativeEvent?: { key?: string }; preventDefault?: () => void }) {
    if (e.nativeEvent?.key === 'Enter') {
      e.preventDefault?.();
      submit();
    }
  }

  return (
    <Modal visible={visible} transparent animationType="fade" onRequestClose={onCancel}>
      <View
        style={[
          styles.backdrop,
          { paddingTop: insets.top, paddingBottom: insets.bottom, paddingLeft: insets.left, paddingRight: insets.right },
        ]}
      >
        <View style={styles.card}>
          <Text style={styles.title}>{title}</Text>
          {subtitle ? <Text style={styles.subtitle}>{subtitle}</Text> : null}
          <TextInput
            value={pin}
            onChangeText={setPin}
            placeholder="PIN"
            placeholderTextColor={colors.textMuted}
            keyboardType="number-pad"
            secureTextEntry
            style={[styles.input, errorText ? styles.inputError : null]}
            autoFocus
            returnKeyType="done"
            blurOnSubmit
            onSubmitEditing={submit}
            onKeyPress={handleKeyPress}
          />
          {errorText ? (
            <Text style={styles.errorText}>
              {errorText === 'invalid_pin' ? 'Wrong PIN. Try again.' : errorText}
            </Text>
          ) : null}
          <View style={styles.row}>
            <Pressable
              style={[styles.btn, { backgroundColor: colors.surface }]}
              onPress={() => {
                setPin('');
                onCancel();
              }}
            >
              <Text style={styles.btnGhostText}>Cancel</Text>
            </Pressable>
            <Pressable style={[styles.btn, { backgroundColor: colors.good }]} onPress={submit}>
              <Text style={styles.btnPrimaryText}>{busy ? 'Checking...' : 'Confirm'}</Text>
            </Pressable>
          </View>
        </View>
      </View>
    </Modal>
  );
}
