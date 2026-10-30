import React from 'react';
import { Pressable, ScrollView, Text, View } from 'react-native';
import { useRouter, usePathname } from 'expo-router';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { createThemedStyles } from '../theme/ThemeContext';
import { colors } from '../theme/colors';
import { ConnectionBadge } from './ConnectionBadge';
import { useConnectionStore } from '../stores/connectionStore';
import { useAuthStore } from '../stores/authStore';
import { useResponsive } from '../hooks/useResponsive';

const TABS = [
  { href: '/', label: 'Overview' },
  { href: '/pipelines', label: 'Pipelines' },
  { href: '/alerts', label: 'Alerts' },
  { href: '/scheduler', label: 'Scheduler' },
  { href: '/downtime', label: 'Downtime' },
  { href: '/notifications', label: 'Notifications' },
] as const;

const useStyles = createThemedStyles((c) => ({
  bar: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'space-between',
    paddingHorizontal: 16,
    paddingTop: 10,
    paddingBottom: 10,
    borderBottomWidth: 1,
    borderBottomColor: c.border,
    backgroundColor: c.background,
  },
  barPhone: { flexDirection: 'column', alignItems: 'stretch', gap: 8 },
  tabs: { flexDirection: 'row', gap: 6 },
  tabsScroll: { flexGrow: 0 },
  rightPhone: { justifyContent: 'space-between' },
  tab: { paddingHorizontal: 14, paddingVertical: 8, borderRadius: 8 },
  tabText: { fontSize: 13, fontWeight: '700', color: c.textSecondary },
  tabTextActive: { color: c.textPrimary },
  brand: { color: c.textMuted, fontSize: 11, letterSpacing: 1, flexShrink: 1 },
  right: { flexDirection: 'row', alignItems: 'center', gap: 10, flexShrink: 1 },
  lockBtn: { paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8, borderWidth: 1, borderColor: c.border },
  lockBtnText: { fontSize: 11, fontWeight: '700', color: c.textSecondary, letterSpacing: 0.5 },
}));

export function AppNav() {
  const styles = useStyles();
  const router = useRouter();
  const pathname = usePathname();
  const insets = useSafeAreaInsets();
  const { isPhone } = useResponsive();
  const connectionState = useConnectionStore((s) => s.connectionState);
  const source = useConnectionStore((s) => s.source);
  const logout = useAuthStore((s) => s.logout);
  const account = useAuthStore((s) => s.account);

  const tabsEl = (
    <View style={styles.tabs}>
      {TABS.map((t) => {
        const active = pathname === t.href || (t.href !== '/' && pathname.startsWith(`${t.href}/`));
        return (
          <Pressable
            key={t.href}
            onPress={() => {
              if (!active) router.replace(t.href);
            }}
            style={[styles.tab, active ? { backgroundColor: colors.surface } : null]}
          >
            <Text style={[styles.tabText, active ? styles.tabTextActive : null]}>{t.label}</Text>
          </Pressable>
        );
      })}
    </View>
  );

  return (
    <View style={[styles.bar, isPhone ? styles.barPhone : null, { paddingTop: 10 + insets.top, paddingLeft: 16 + insets.left, paddingRight: 16 + insets.right }]}>
      {isPhone ? (
        <ScrollView horizontal showsHorizontalScrollIndicator={false} style={styles.tabsScroll}>
          {tabsEl}
        </ScrollView>
      ) : (
        tabsEl
      )}
      <View style={[styles.right, isPhone ? styles.rightPhone : null]}>
        <ConnectionBadge state={connectionState} source={source} />
        {account ? <Text style={styles.brand} numberOfLines={1}>{account}</Text> : null}
        <Pressable style={styles.lockBtn} onPress={() => void logout()}>
          <Text style={styles.lockBtnText}>LOCK</Text>
        </Pressable>
      </View>
    </View>
  );
}
