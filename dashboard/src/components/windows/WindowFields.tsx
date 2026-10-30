import React from 'react';
import { Text, View } from 'react-native';
import { createThemedStyles } from '../../theme/ThemeContext';
import { Chip, FormField, Input } from '../scheduler/ui';
import { DAY_LABELS, deviceTimezone, crossesMidnight } from '../../utils/timeWindow';
import type { AccountWindow } from '../../types/downtime';

const useStyles = createThemedStyles((c) => ({
  wrap: { gap: 12 },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 6 },
  times: { flexDirection: 'row', gap: 12 },
  time: { flex: 1, minWidth: 100 },
  hint: { color: c.textMuted, fontSize: 11 },
}));

interface Props {
  value: AccountWindow;
  onChange: (next: AccountWindow) => void;
}

export function WindowFields({ value, onChange }: Props) {
  const styles = useStyles();
  const toggleDay = (d: number) => {
    const days = value.days.includes(d) ? value.days.filter((x) => x !== d) : [...value.days, d].sort((a, b) => a - b);
    onChange({ ...value, days });
  };
  return (
    <View style={styles.wrap}>
      <FormField label="Timezone" hint="IANA name, e.g. America/Chicago or Asia/Karachi">
        <Input value={value.timezone} onChangeText={(timezone) => onChange({ ...value, timezone })} />
        <View style={styles.row}>
          <Chip label="Use this device's timezone" onPress={() => onChange({ ...value, timezone: deviceTimezone() })} />
        </View>
      </FormField>
      <FormField label="Days">
        <View style={styles.row}>
          {DAY_LABELS.map((label, d) => (
            <Chip key={label} label={label} active={value.days.includes(d)} onPress={() => toggleDay(d)} />
          ))}
        </View>
        <View style={styles.row}>
          <Chip label="Mon-Fri" onPress={() => onChange({ ...value, days: [0, 1, 2, 3, 4] })} />
          <Chip label="Every day" onPress={() => onChange({ ...value, days: [0, 1, 2, 3, 4, 5, 6] })} />
        </View>
      </FormField>
      <View style={styles.times}>
        <View style={styles.time}>
          <FormField label="Start (HH:MM)">
            <Input value={value.start} onChangeText={(start) => onChange({ ...value, start })} placeholder="09:00" maxLength={5} />
          </FormField>
        </View>
        <View style={styles.time}>
          <FormField label="End (HH:MM)">
            <Input value={value.end} onChangeText={(end) => onChange({ ...value, end })} placeholder="17:00" maxLength={5} />
          </FormField>
        </View>
      </View>
      {crossesMidnight(value) ? <Text style={styles.hint}>End is not after start, so this window runs past midnight into the next day.</Text> : null}
    </View>
  );
}
