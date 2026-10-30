import React, { useState } from 'react';
import { Text, TextInput, View, Pressable } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { createThemedStyles } from '../theme/ThemeContext';
import { colors } from '../theme/colors';
import { useAuthStore } from '../stores/authStore';

const useStyles = createThemedStyles((c) => ({
  screen: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: c.background,
    padding: 20,
  },
  card: {
    width: '100%',
    maxWidth: 380,
    alignItems: 'center',
    gap: 14,
    paddingVertical: 32,
    paddingHorizontal: 28,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: c.border,
    backgroundColor: c.surface,
  },
  title: { fontSize: 22, fontWeight: '800', color: c.textPrimary, letterSpacing: 0.5 },
  subtitle: { fontSize: 13, color: c.textSecondary, textAlign: 'center' },
  input: {
    width: '100%',
    backgroundColor: c.background,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: c.border,
    color: c.textPrimary,
    fontSize: 22,
    letterSpacing: 6,
    textAlign: 'center',
    paddingVertical: 12,
    marginTop: 6,
  },
  inputError: { borderColor: colors.bad },
  errorText: { color: colors.bad, fontSize: 13, fontWeight: '700', textAlign: 'center' },
  btn: { width: '100%', borderRadius: 8, paddingVertical: 12, alignItems: 'center', marginTop: 4 },
  btnText: { color: '#0d1117', fontWeight: '800', fontSize: 15 },
  brand: { color: c.textMuted, fontSize: 11, letterSpacing: 1, marginTop: 18 },
}));

export function AccessGateScreen() {
  const styles = useStyles();
  const insets = useSafeAreaInsets();
  const error = useAuthStore((s) => s.error);
  const login = useAuthStore((s) => s.login);
  const clearError = useAuthStore((s) => s.clearError);
  const [code, setCode] = useState('');
  const [submitting, setSubmitting] = useState(false);

  async function submit() {
    if (code.trim().length === 0 || submitting) return;
    setSubmitting(true);
    const ok = await login(code);
    setSubmitting(false);
    if (!ok) setCode('');
  }

  function handleKeyPress(e: { nativeEvent?: { key?: string }; preventDefault?: () => void }) {
    // Same Enter-vs-<form> guard as PinPromptModal.
    if (e.nativeEvent?.key === 'Enter') {
      e.preventDefault?.();
      submit();
    }
  }

  return (
    <View
      style={[
        styles.screen,
        { paddingTop: 20 + insets.top, paddingBottom: 20 + insets.bottom, paddingLeft: 20 + insets.left, paddingRight: 20 + insets.right },
      ]}
    >
      <View style={styles.card}>
        <Text style={styles.title}>SB Monitoring Dashboard</Text>
        <Text style={styles.subtitle}>Sign in with your account secret.</Text>
        <TextInput
          value={code}
          onChangeText={(v) => {
            setCode(v);
            if (error) clearError();
          }}
          placeholder="Account secret"
          placeholderTextColor={colors.textMuted}
          secureTextEntry
          autoFocus
          autoCapitalize="none"
          autoCorrect={false}
          returnKeyType="done"
          blurOnSubmit
          onSubmitEditing={submit}
          onKeyPress={handleKeyPress}
          style={[styles.input, error ? styles.inputError : null]}
        />
        {error ? <Text style={styles.errorText}>{error}</Text> : null}
        <Pressable
          style={[styles.btn, { backgroundColor: colors.good, opacity: submitting ? 0.6 : 1 }]}
          onPress={submit}
          disabled={submitting}
        >
          <Text style={styles.btnText}>{submitting ? 'Signing in…' : 'Sign in'}</Text>
        </Pressable>
      </View>
      <Text style={styles.brand}>SERVICE BUS MONITORING</Text>
    </View>
  );
}
