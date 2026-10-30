import React from 'react';
import { Text, View } from 'react-native';
import { createThemedStyles } from '../../theme/ThemeContext';
import { colors } from '../../theme/colors';
import { useConnectionStore } from '../../stores/connectionStore';
import { formatTimeAgo } from '../../utils/format';
import type { StandaloneServiceNode } from '../../types/relay';

const useStyles = createThemedStyles((c) => ({
  wrap: { gap: 14, marginTop: 10 },
  section: { gap: 8 },
  heading: { color: c.textMuted, fontSize: 12, fontWeight: '800', textTransform: 'uppercase', letterSpacing: 0.6 },
  subheading: { color: c.textMuted, fontSize: 11, marginTop: -4 },
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
    borderStyle: 'dashed',
  },
  dot: { width: 9, height: 9, borderRadius: 5 },
  name: { color: c.textPrimary, fontSize: 13, fontWeight: '700' },
  meta: { color: c.textMuted, fontSize: 11 },
}));

function serviceStatusVisual(node: StandaloneServiceNode): { color: string; text: string } {
  switch (node.status) {
    case 'ok':
      return { color: colors.good, text: 'Confirmed healthy' };
    case 'down':
      return { color: colors.bad, text: 'Confirmed broken (down)' };
    case 'blocked':
      return { color: colors.blocked, text: 'Blocked' };
    default:
      return { color: colors.unknown, text: 'Unknown -- not currently reporting' };
  }
}

export function ExternalInfraStrip() {
  const styles = useStyles();
  const services = useConnectionStore((s) => s.estateServices);

  if (services.length === 0) return null;

  return (
    <View style={styles.wrap}>
      {services.length > 0 ? (
        <View style={styles.section}>
          <Text style={styles.heading}>No machine / external services</Text>
          <Text style={styles.subheading}>
            Standalone services (e.g. a managed/hosted database) with no OS host this relay controls -- status comes
            only from this service's own probe, never from any machine's liveness.
          </Text>
          <View style={styles.row}>
            {services.map((node) => {
              const { color, text } = serviceStatusVisual(node);
              return (
                <View key={node.node_id} style={styles.card}>
                  <View style={[styles.dot, { backgroundColor: color }]} />
                  <Text style={styles.name}>{node.node_name}</Text>
                  <Text style={styles.meta}>
                    {node.node_type} · {node.node_platform} · {text}
                    {node.last_known_ts ? ` · last known ${formatTimeAgo(node.last_known_ts)}` : ''}
                  </Text>
                </View>
              );
            })}
          </View>
        </View>
      ) : null}
    </View>
  );
}
