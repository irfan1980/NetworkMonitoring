import React, { useMemo } from 'react';
import { ScrollView, Text, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { createThemedStyles } from '../src/theme/ThemeContext';
import { useConnectionStore } from '../src/stores/connectionStore';
import { useResponsive } from '../src/hooks/useResponsive';
import { ObjectStrip } from '../src/components/ObjectStrip';
import { BusesStrip } from '../src/components/BusesStrip';
import { PipelineCard } from '../src/components/PipelineCard';
import { usePipelinePlatformMap, usePlatformList } from '../src/utils/platformGrouping';

const useStyles = createThemedStyles((c) => ({
  screen: { flex: 1, backgroundColor: c.background },
  content: { padding: 16, gap: 12, flexGrow: 1 },
  heading: { color: c.textPrimary, fontSize: 20, fontWeight: '800' },
  groupHeading: { color: c.textSecondary, fontSize: 14, fontWeight: '800', textTransform: 'uppercase', letterSpacing: 0.5, marginTop: 4 },
  grid: { flexDirection: 'row', flexWrap: 'wrap', gap: 12 },
}));

const WALL_COLUMNS = 3;

export default function PipelinesScreen() {
  const styles = useStyles();
  const router = useRouter();
  const pipelines = useConnectionStore((s) => s.pipelines);
  const objects = useConnectionStore((s) => s.objects);
  const { isWall, height } = useResponsive();
  const insets = useSafeAreaInsets();
  const platformList = usePlatformList();
  const pipelinePlatform = usePipelinePlatformMap();

  const groups = useMemo(() => {
    const list = Object.values(pipelines).sort((a, b) =>
      String(a.pipelineId).localeCompare(String(b.pipelineId), undefined, { numeric: true })
    );
    const byPlatform = new Map<string, typeof list>();
    for (const p of list) {
      const platformId = pipelinePlatform[String(p.pipelineId)] ?? 'other';
      if (!byPlatform.has(platformId)) byPlatform.set(platformId, []);
      byPlatform.get(platformId)!.push(p);
    }
    const orderedIds = [...platformList.map((p) => p.id), ...[...byPlatform.keys()].filter((id) => id === 'other')];
    return orderedIds
      .filter((id) => byPlatform.has(id))
      .map((id) => ({
        id,
        label: platformList.find((p) => p.id === id)?.label ?? 'Other',
        pipelines: byPlatform.get(id)!,
      }));
  }, [pipelines, pipelinePlatform, platformList]);

  const totalRows = useMemo(
    () => groups.reduce((sum, g) => sum + Math.ceil(g.pipelines.length / WALL_COLUMNS), 0) || 1,
    [groups]
  );

  const cardMinHeight = useMemo(() => {
    if (!isWall) return undefined;
    const reservedForChrome = 220 + groups.length * 30;
    return Math.max(180, Math.floor((height - reservedForChrome) / totalRows) - 12);
  }, [isWall, groups.length, totalRows, height]);

  return (
    <ScrollView
      style={styles.screen}
      contentContainerStyle={[
        styles.content,
        { paddingLeft: 16 + insets.left, paddingRight: 16 + insets.right, paddingBottom: 16 + insets.bottom },
      ]}
    >
      <Text style={styles.heading}>Pipelines</Text>
      <ObjectStrip objects={Object.values(objects)} />
      <BusesStrip />
      {groups.map((group) => (
        <View key={group.id} style={{ gap: 8 }}>
          <Text style={styles.groupHeading}>{group.label}</Text>
          <View style={styles.grid}>
            {group.pipelines.map((p) => (
              <PipelineCard
                key={p.pipelineId}
                pipeline={p}
                minHeight={cardMinHeight}
                large={isWall}
                onPress={() => router.push(`/pipeline/${encodeURIComponent(String(p.pipelineId))}`)}
              />
            ))}
          </View>
        </View>
      ))}
    </ScrollView>
  );
}
