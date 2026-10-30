import React from 'react';
import { ScrollView, Text } from 'react-native';
import { useRouter } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { createThemedStyles } from '../../src/theme/ThemeContext';
import { usePinSession } from '../../src/hooks/usePinSession';
import { ScheduleEditor } from '../../src/components/scheduler/ScheduleEditor';

const useStyles = createThemedStyles((c) => ({
  screen: { flex: 1, backgroundColor: c.background },
  content: { padding: 16, gap: 16 },
  heading: { color: c.textPrimary, fontSize: 20, fontWeight: '800' },
}));

export default function NewScheduleScreen() {
  const styles = useStyles();
  const router = useRouter();
  const insets = useSafeAreaInsets();
  const pin = usePinSession();

  return (
    <ScrollView
      style={styles.screen}
      keyboardShouldPersistTaps="handled"
      contentContainerStyle={[styles.content, { paddingLeft: 16 + insets.left, paddingRight: 16 + insets.right, paddingBottom: 16 + insets.bottom }]}
    >
      <Text style={styles.heading}>New schedule</Text>
      <ScheduleEditor
        pin={pin}
        onSaved={(d) => router.replace(`/scheduler/${d.id}`)}
        onCancel={() => (router.canGoBack() ? router.back() : router.replace('/scheduler'))}
      />
      {pin.modal}
    </ScrollView>
  );
}
