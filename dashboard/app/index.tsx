import React, { useCallback, useMemo, useState } from 'react';
import type { LayoutChangeEvent } from 'react-native';
import { ScrollView, View } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { createThemedStyles } from '../src/theme/ThemeContext';
import { useResponsive } from '../src/hooks/useResponsive';
import { Legend } from '../src/components/topology/Legend';
import { TopologyGraph } from '../src/components/topology/TopologyGraph';

const useStyles = createThemedStyles((c) => ({
  screen: { flex: 1, backgroundColor: c.background },
  scrollContent: { padding: 16, gap: 8, alignItems: 'center' },
  fitScreen: { flex: 1, minWidth: 0, padding: 16, gap: 8 },
}));

const GRAPH_ASPECT = 1.85;

export default function OverviewScreen() {
  const styles = useStyles();
  const { isPhone, width: winWidth, height: winHeight } = useResponsive();
  const router = useRouter();
  const insets = useSafeAreaInsets();

  const [containerHeight, setContainerHeight] = useState(0);
  const [containerWidth, setContainerWidth] = useState(0);
  const [canvasTouched, setCanvasTouched] = useState(false);

  const onContainerLayout = useCallback((e: LayoutChangeEvent) => {
    setContainerHeight(e.nativeEvent.layout.height);
    setContainerWidth(e.nativeEvent.layout.width);
  }, []);

  const maxGraphWidth = isPhone
    ? Math.min(winWidth - 32, 1880)
    : (containerWidth > 0 ? containerWidth : winWidth) - 32;

  const { graphWidth, graphHeight } = useMemo(() => {
    if (isPhone) {
      const w = maxGraphWidth;
      const PHONE_CHROME_ESTIMATE = 260;
      const h = Math.max(360, winHeight - PHONE_CHROME_ESTIMATE);
      return { graphWidth: w, graphHeight: h };
    }
    const available = Math.max(220, containerHeight - 24);
    const w = maxGraphWidth;
    const h = containerHeight > 0 ? available : w / GRAPH_ASPECT;
    return { graphWidth: w, graphHeight: h };
  }, [isPhone, maxGraphWidth, containerHeight, winHeight]);

  if (isPhone) {
    return (
      <ScrollView
        scrollEnabled={!canvasTouched}
        style={styles.screen}
        contentContainerStyle={[
          styles.scrollContent,
          { paddingLeft: 16 + insets.left, paddingRight: 16 + insets.right, paddingBottom: 16 + insets.bottom },
        ]}
      >
        <View>
          <TopologyGraph
            width={graphWidth}
            height={graphHeight}
            onTouchActive={setCanvasTouched}
            onSelectPipeline={(pipelineId) => router.push(`/pipeline/${encodeURIComponent(String(pipelineId))}`)}
          />
        </View>
        <Legend />
      </ScrollView>
    );
  }

  return (
    <View style={styles.screen}>
      <View style={styles.fitScreen} onLayout={onContainerLayout}>
        <TopologyGraph
          width={graphWidth}
          height={graphHeight}
          onSelectPipeline={(pipelineId) => router.push(`/pipeline/${encodeURIComponent(String(pipelineId))}`)}
        />
      </View>
    </View>
  );
}
