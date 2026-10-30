import React, { useEffect } from 'react';
import { Modal, Platform, Pressable, ScrollView, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { createThemedStyles } from '../../theme/ThemeContext';
import { useResponsive } from '../../hooks/useResponsive';

const useStyles = createThemedStyles((c) => ({
  backdrop: { flex: 1, backgroundColor: 'rgba(0,0,0,0.7)' },
  backdropWall: { alignItems: 'center', justifyContent: 'center', padding: 24 },
  cardWall: {
    backgroundColor: c.surfaceRaised,
    borderRadius: 20,
    padding: 28,
    width: '100%',
    maxWidth: 980,
    maxHeight: '90%',
    borderWidth: 1,
    borderColor: c.border,
    gap: 16,
  },
  cardSheet: { flex: 1, backgroundColor: c.surfaceRaised, gap: 16, padding: 20 },
  headerRow: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 },
  title: { color: c.textPrimary, fontSize: 24, fontWeight: '900', flexShrink: 1 },
  closeBtn: {
    paddingHorizontal: 16,
    paddingVertical: 10,
    borderRadius: 10,
    backgroundColor: c.surface,
    borderWidth: 1,
    borderColor: c.border,
  },
  closeText: { color: c.textSecondary, fontWeight: '800', fontSize: 15 },
}));

interface Props {
  visible: boolean;
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}

export function DiagnosticsModal({ visible, title, onClose, children }: Props) {
  const styles = useStyles();
  const { isPhone } = useResponsive();
  const insets = useSafeAreaInsets();

  useEffect(() => {
    if (!visible || Platform.OS !== 'web') return;
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') onClose();
    }
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [visible, onClose]);

  return (
    <Modal visible={visible} transparent={!isPhone} animationType={isPhone ? 'slide' : 'fade'} onRequestClose={onClose}>
      <View style={[styles.backdrop, !isPhone && styles.backdropWall]}>
        <View
          style={[
            isPhone ? styles.cardSheet : styles.cardWall,
            isPhone ? { paddingTop: insets.top + 12, paddingBottom: insets.bottom + 12 } : null,
          ]}
        >
          <View style={styles.headerRow}>
            <Text style={styles.title}>{title}</Text>
            <Pressable style={styles.closeBtn} onPress={onClose}>
              <Text style={styles.closeText}>Close</Text>
            </Pressable>
          </View>
          <ScrollView showsVerticalScrollIndicator={!isPhone}>{children}</ScrollView>
        </View>
      </View>
    </Modal>
  );
}
