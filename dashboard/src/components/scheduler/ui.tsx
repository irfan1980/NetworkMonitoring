import React from 'react';
import { Pressable, Text, TextInput, View, type TextInputProps } from 'react-native';
import { createThemedStyles } from '../../theme/ThemeContext';
import { colors } from '../../theme/colors';

const useStyles = createThemedStyles((c) => ({
  btn: { paddingHorizontal: 14, paddingVertical: 9, borderRadius: 8, alignItems: 'center', justifyContent: 'center', borderWidth: 1 },
  btnText: { fontSize: 13, fontWeight: '800' },
  chip: { paddingHorizontal: 12, paddingVertical: 7, borderRadius: 999, borderWidth: 1, borderColor: c.border, backgroundColor: c.surface },
  chipActive: { borderColor: colors.accent, backgroundColor: colors.blockedDim },
  chipText: { fontSize: 12, fontWeight: '700', color: c.textSecondary },
  chipTextActive: { color: c.textPrimary },
  pill: { paddingHorizontal: 8, paddingVertical: 2, borderRadius: 999 },
  pillText: { fontSize: 11, fontWeight: '800' },
  field: { gap: 4 },
  fieldLabel: { color: c.textMuted, fontSize: 11, fontWeight: '800', textTransform: 'uppercase', letterSpacing: 0.5 },
  fieldHint: { color: c.textMuted, fontSize: 11 },
  fieldError: { color: colors.bad, fontSize: 12, fontWeight: '700' },
  input: {
    backgroundColor: c.background,
    borderWidth: 1,
    borderColor: c.border,
    borderRadius: 8,
    color: c.textPrimary,
    fontSize: 14,
    paddingHorizontal: 10,
    paddingVertical: 8,
  },
  inputError: { borderColor: colors.bad },
}));

type Tone = 'primary' | 'ghost' | 'danger';

interface ButtonProps {
  label: string;
  onPress: () => void;
  tone?: Tone;
  disabled?: boolean;
}

export function ActionButton({ label, onPress, tone = 'ghost', disabled }: ButtonProps) {
  const styles = useStyles();
  const palette =
    tone === 'primary'
      ? { bg: colors.goodDim, border: colors.good, text: colors.good }
      : tone === 'danger'
        ? { bg: colors.badDim, border: colors.bad, text: colors.bad }
        : { bg: colors.surface, border: colors.border, text: colors.textSecondary };
  return (
    <Pressable
      onPress={onPress}
      disabled={disabled}
      style={[styles.btn, { backgroundColor: palette.bg, borderColor: palette.border, opacity: disabled ? 0.5 : 1 }]}
    >
      <Text style={[styles.btnText, { color: palette.text }]}>{label}</Text>
    </Pressable>
  );
}

interface ChipProps {
  label: string;
  active?: boolean;
  onPress: () => void;
}

export function Chip({ label, active, onPress }: ChipProps) {
  const styles = useStyles();
  return (
    <Pressable onPress={onPress} style={[styles.chip, active ? styles.chipActive : null]}>
      <Text style={[styles.chipText, active ? styles.chipTextActive : null]}>{label}</Text>
    </Pressable>
  );
}

export function Pill({ label, color }: { label: string; color: string }) {
  const styles = useStyles();
  return (
    <View style={[styles.pill, { backgroundColor: `${color}26` }]}>
      <Text style={[styles.pillText, { color }]}>{label}</Text>
    </View>
  );
}

interface FieldProps {
  label?: string;
  hint?: string;
  errors?: string[];
  children: React.ReactNode;
}

export function FormField({ label, hint, errors, children }: FieldProps) {
  const styles = useStyles();
  return (
    <View style={styles.field}>
      {label ? <Text style={styles.fieldLabel}>{label}</Text> : null}
      {children}
      {hint ? <Text style={styles.fieldHint}>{hint}</Text> : null}
      {errors?.map((m, i) => (
        <Text key={i} style={styles.fieldError}>
          {m}
        </Text>
      ))}
    </View>
  );
}

export function Input({ invalid, style, ...rest }: TextInputProps & { invalid?: boolean }) {
  const styles = useStyles();
  return (
    <TextInput
      placeholderTextColor={colors.textMuted}
      autoCapitalize="none"
      autoCorrect={false}
      {...rest}
      style={[styles.input, invalid ? styles.inputError : null, style]}
    />
  );
}
