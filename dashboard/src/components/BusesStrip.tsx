import React, { useMemo } from 'react';
import { Text, View } from 'react-native';
import { createThemedStyles } from '../theme/ThemeContext';
import { colors } from '../theme/colors';
import { useConnectionStore } from '../stores/connectionStore';
import { usePlatformList } from '../utils/platformGrouping';
import { busDisplayName } from '../utils/health';

const useStyles = createThemedStyles((c) => ({
  section: { gap: 8, marginTop: 4 },
  heading: { color: c.textSecondary, fontSize: 14, fontWeight: '800', textTransform: 'uppercase', letterSpacing: 0.5 },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 10 },
  card: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    backgroundColor: c.surface,
    borderRadius: 10,
    paddingHorizontal: 12,
    paddingVertical: 8,
    borderWidth: 1,
    borderColor: c.border,
  },
  dot: { width: 9, height: 9, borderRadius: 5 },
  name: { color: c.textPrimary, fontSize: 13, fontWeight: '700' },
  servers: { color: c.textMuted, fontSize: 12 },
}));

export function BusesStrip() {
  const styles = useStyles();
  const buses = useConnectionStore((s) => s.buses);
  const platformList = usePlatformList();

  const rows = useMemo(() => {
    return Object.values(buses).map((bus) => ({
      bus,
      servers: platformList.filter((p) => (p.usedBuses ?? []).includes(bus.id)).map((p) => p.label),
    }));
  }, [buses, platformList]);

  if (rows.length === 0) return null;

  return (
    <View style={styles.section}>
      <Text style={styles.heading}>Buses</Text>
      <View style={styles.row}>
        {rows.map(({ bus, servers }) => (
          <View key={bus.id} style={styles.card}>
            <View style={[styles.dot, { backgroundColor: bus.ok ? colors.good : colors.unknown }]} />
            <Text style={styles.name}>{busDisplayName(bus)}</Text>
            <Text style={styles.servers}>{servers.length > 0 ? `used by ${servers.join(', ')}` : 'not in use'}</Text>
          </View>
        ))}
      </View>
    </View>
  );
}
