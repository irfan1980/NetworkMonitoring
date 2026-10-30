import React, { useMemo } from 'react';
import { ScrollView, Text, View } from 'react-native';
import { createThemedStyles } from '../theme/ThemeContext';
import { objectColor, objectLabelFor } from '../utils/health';
import { useResponsive } from '../hooks/useResponsive';
import { useConnectionStore } from '../stores/connectionStore';
import type { MonitoredObject } from '../types/relay';

const useStyles = createThemedStyles((c) => ({
  row: { flexDirection: 'row', gap: 8, paddingVertical: 8, paddingRight: 28 },
  chip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 6,
    backgroundColor: c.surface,
    borderRadius: 999,
    paddingHorizontal: 10,
    paddingVertical: 6,
  },
  dot: { width: 8, height: 8, borderRadius: 4 },
  labelCol: { flexDirection: 'column' },
  label: { fontSize: 12, color: c.textSecondary },
  rawId: { fontSize: 9, color: c.textMuted },
  hint: { fontSize: 16, color: c.textMuted, alignSelf: 'center', paddingLeft: 4 },
}));

export function ObjectStrip({ objects }: { objects: MonitoredObject[] }) {
  const styles = useStyles();
  const { isPhone } = useResponsive();
  const buses = useConnectionStore((s) => s.buses);

  const labelFor = useMemo(() => (o: MonitoredObject) => objectLabelFor(o, buses), [buses]);

  return (
    <View style={{ flexDirection: 'row', alignItems: 'center', width: '100%' }}>
      <ScrollView horizontal showsHorizontalScrollIndicator={isPhone} style={{ flex: 1, flexShrink: 1 }}>
        <View style={styles.row}>
          {objects.map((o) => {
            const label = labelFor(o);
            return (
              <View key={o.id} style={styles.chip}>
                <View style={[styles.dot, { backgroundColor: objectColor(o.status) }]} />
                <View style={styles.labelCol}>
                  <Text style={styles.label}>{label}</Text>
                  {label !== o.id ? <Text style={styles.rawId}>{o.id}</Text> : null}
                </View>
              </View>
            );
          })}
        </View>
      </ScrollView>
      {isPhone ? <Text style={styles.hint}>›</Text> : null}
    </View>
  );
}
