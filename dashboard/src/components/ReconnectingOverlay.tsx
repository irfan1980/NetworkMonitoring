import React, { useEffect, useRef, useState } from 'react';
import { AccessibilityInfo, Animated, Easing, Platform, Text, View, type ViewStyle } from 'react-native';
import Svg, { Circle, Path } from 'react-native-svg';
import { RELAY_WS_URL } from '../config/env';
import { useResponsive } from '../hooks/useResponsive';
import { createThemedStyles } from '../theme/ThemeContext';
import { colors } from '../theme/colors';
import { FONT_FAMILY } from '../theme/typography';
import { formatDuration } from '../utils/format';

const BLUR_SUPPORTED =
  Platform.OS === 'web' &&
  typeof globalThis !== 'undefined' &&
  typeof (globalThis as { CSS?: { supports?: (v: string) => boolean } }).CSS?.supports === 'function' &&
  ((globalThis as any).CSS.supports('backdrop-filter: blur(6px)') ||
    (globalThis as any).CSS.supports('-webkit-backdrop-filter: blur(6px)'));

const scrimStyle: ViewStyle = BLUR_SUPPORTED
  ? ({
      backgroundColor: 'rgba(13, 17, 23, 0.55)',
      backdropFilter: 'blur(7px)',
      WebkitBackdropFilter: 'blur(7px)',
    } as unknown as ViewStyle)
  : { backgroundColor: 'rgba(13, 17, 23, 0.92)' };

const useStyles = createThemedStyles((c) => ({
  overlay: {
    position: 'absolute',
    top: 0,
    left: 0,
    right: 0,
    bottom: 0,
    alignItems: 'center',
    justifyContent: 'center',
    padding: 20,
    zIndex: 1000,
    elevation: 40,
  },
  card: {
    width: '100%',
    maxWidth: 480,
    alignItems: 'center',
    gap: 14,
    paddingVertical: 28,
    paddingHorizontal: 24,
    borderRadius: 16,
    borderWidth: 1,
    borderColor: c.border,
    backgroundColor: 'rgba(22, 27, 34, 0.92)',
  },
  headline: { fontSize: 26, fontWeight: '800', color: c.textPrimary, letterSpacing: 1 },
  headlinePhone: { fontSize: 20 },
  lead: { fontSize: 14, lineHeight: 20, color: c.textSecondary, textAlign: 'center' },
  staleChip: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 6,
    borderRadius: 999,
    borderWidth: 1,
    borderColor: c.warn,
    backgroundColor: c.warnDim,
  },
  staleText: { fontSize: 12, fontWeight: '700', color: c.warn, letterSpacing: 0.4 },
  facts: { alignSelf: 'stretch', gap: 8, marginTop: 4 },
  factRow: { flexDirection: 'row', alignItems: 'flex-start', gap: 10 },
  factLabel: { width: 132, fontSize: 12, color: c.textMuted, letterSpacing: 0.4, textTransform: 'uppercase' },
  factValue: { flex: 1, fontSize: 13, color: c.textPrimary, fontWeight: '600' },
  factValueMono: { flex: 1, fontSize: 12, color: c.textSecondary, fontFamily: FONT_FAMILY },
  timerBlock: { alignItems: 'center', gap: 2 },
  timerLabel: { fontSize: 11, color: c.textMuted, letterSpacing: 1 },
  timer: { fontSize: 32, fontWeight: '800', color: c.warn, letterSpacing: 2 },
}));

function useReducedMotion(): boolean {
  const [reduced, setReduced] = useState(false);

  useEffect(() => {
    if (Platform.OS === 'web') {
      const mq = globalThis.matchMedia?.('(prefers-reduced-motion: reduce)');
      if (!mq) return;
      setReduced(mq.matches);
      const onChange = (e: MediaQueryListEvent) => setReduced(e.matches);
      mq.addEventListener('change', onChange);
      return () => mq.removeEventListener('change', onChange);
    }
    let alive = true;
    AccessibilityInfo.isReduceMotionEnabled().then((v) => {
      if (alive) setReduced(v);
    });
    const sub = AccessibilityInfo.addEventListener('reduceMotionChanged', setReduced);
    return () => {
      alive = false;
      sub.remove();
    };
  }, []);

  return reduced;
}

const SIZE = 84;
const R = 34;

function Spinner({ reduced }: { reduced: boolean }) {
  const anim = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    anim.setValue(0);
    const loop = Animated.loop(
      Animated.timing(anim, {
        toValue: 1,
        duration: reduced ? 1600 : 1100,
        easing: reduced ? Easing.inOut(Easing.quad) : Easing.linear,
        useNativeDriver: Platform.OS !== 'web',
      })
    );
    loop.start();
    return () => loop.stop();
  }, [anim, reduced]);

  const animatedStyle = reduced
    ? { opacity: anim.interpolate({ inputRange: [0, 0.5, 1], outputRange: [1, 0.35, 1] }) }
    : { transform: [{ rotate: anim.interpolate({ inputRange: [0, 1], outputRange: ['0deg', '360deg'] }) }] };

  return (
    <Animated.View style={animatedStyle}>
      <Svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`}>
        <Circle cx={SIZE / 2} cy={SIZE / 2} r={R} stroke={colors.border} strokeWidth={6} fill="none" />
        {reduced ? (
          <Circle cx={SIZE / 2} cy={SIZE / 2} r={R} stroke={colors.warn} strokeWidth={6} fill="none" />
        ) : (
          <Path
            d={`M ${SIZE / 2} ${SIZE / 2 - R} A ${R} ${R} 0 0 1 ${SIZE / 2 + R} ${SIZE / 2}`}
            stroke={colors.warn}
            strokeWidth={6}
            strokeLinecap="round"
            fill="none"
          />
        )}
        <Circle cx={SIZE / 2} cy={SIZE / 2} r={7} fill={colors.warn} opacity={0.85} />
      </Svg>
    </Animated.View>
  );
}

interface Props {
  downSinceMs: number | null;
  attempts: number;
  nextRetryAtMs: number | null;
  everConnected: boolean;
}

export function ReconnectingOverlay({ downSinceMs, attempts, nextRetryAtMs, everConnected }: Props) {
  const styles = useStyles();
  const { isPhone } = useResponsive();
  const reduced = useReducedMotion();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, []);

  const downMs = downSinceMs == null ? 0 : now - downSinceMs;
  const retryInS = nextRetryAtMs == null ? null : Math.ceil((nextRetryAtMs - now) / 1000);
  const retryText =
    attempts === 0
      ? 'opening connection…'
      : retryInS != null && retryInS > 0
        ? `attempt ${attempts} · next try in ${retryInS}s`
        : `attempt ${attempts} · trying now…`;

  return (
    <View
      style={[styles.overlay, scrimStyle]}
      accessibilityViewIsModal
      accessibilityLiveRegion="polite"
    >
      <View style={styles.card}>
        <Spinner reduced={reduced} />
        <Text style={[styles.headline, isPhone && styles.headlinePhone]}>RECONNECTING</Text>
        <Text style={styles.lead}>
          {everConnected
            ? 'Lost the live connection to the monitoring relay. The dashboard behind this panel is frozen at its last update.'
            : 'Waiting for the first snapshot from the monitoring relay. Nothing on this screen is live yet.'}
        </Text>
        <View style={styles.staleChip}>
          <Text style={styles.staleText}>{everConnected ? 'DATA BEHIND IS STALE' : 'NO DATA YET'}</Text>
        </View>
        <View style={styles.timerBlock}>
          <Text style={styles.timerLabel}>DISCONNECTED FOR</Text>
          <Text style={styles.timer}>{formatDuration(downMs)}</Text>
        </View>
        <View style={styles.facts}>
          <View style={styles.factRow}>
            <Text style={styles.factLabel}>Relay</Text>
            <Text style={styles.factValueMono}>{RELAY_WS_URL}</Text>
          </View>
          <View style={styles.factRow}>
            <Text style={styles.factLabel}>Reconnect</Text>
            <Text style={styles.factValue}>{retryText}</Text>
          </View>
        </View>
      </View>
    </View>
  );
}
