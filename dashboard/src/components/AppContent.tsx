import React, { useState } from 'react';
import { View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { createThemedStyles } from '../theme/ThemeContext';
import { useConnectionStore } from '../stores/connectionStore';
import { useResponsive } from '../hooks/useResponsive';
import { useOverallStatus } from '../hooks/useOverallStatus';
import { useSidebarVisible } from '../hooks/useSidebarVisible';
import { IssuesSidebar } from './IssuesSidebar';
import { HealthPill } from './HealthPill';
import { DevPanel } from './DevPanel';

const useStyles = createThemedStyles(() => ({
  content: { flex: 1, minHeight: 0 },
  row: { flex: 1, flexDirection: 'row', minHeight: 0, overflow: 'hidden' },
  main: { flex: 1, minWidth: 0 },
  sidebarWrap: { paddingVertical: 16, paddingRight: 16, overflow: 'hidden' },
  healthPillWrap: { position: 'absolute', top: 8, right: 8, zIndex: 5 },
}));

interface Props {
  children: React.ReactNode;
  overlay?: React.ReactNode;
}

export function AppContent({ children, overlay }: Props) {
  const styles = useStyles();
  const { isPhone } = useResponsive();
  const insets = useSafeAreaInsets();
  const source = useConnectionStore((s) => s.source);
  const status = useOverallStatus();
  const showSidebar = useSidebarVisible();
  const [devPanelVisible, setDevPanelVisible] = useState(false);

  const openDevPanel = () => {
    if (source === 'mock') setDevPanelVisible(true);
  };

  return (
    <View style={styles.content}>
      {isPhone ? (
        <View style={{ paddingTop: 16, paddingLeft: 16 + insets.left, paddingRight: 16 + insets.right }}>
          <IssuesSidebar status={status} onLongPressTitle={openDevPanel} />
        </View>
      ) : null}
      <View style={styles.row}>
        <View style={styles.main}>{children}</View>
        {!isPhone && showSidebar ? (
          <View style={styles.sidebarWrap}>
            <IssuesSidebar status={status} onLongPressTitle={openDevPanel} />
          </View>
        ) : null}
      </View>
      {!isPhone && !showSidebar ? (
        <View style={styles.healthPillWrap}>
          <HealthPill status={status} onLongPress={openDevPanel} />
        </View>
      ) : null}
      {source === 'mock' ? <DevPanel visible={devPanelVisible} onClose={() => setDevPanelVisible(false)} /> : null}
      {overlay}
    </View>
  );
}
