import React, { forwardRef, useEffect, useImperativeHandle, useRef, useState } from 'react';
import { Platform, Pressable, StyleSheet, Text, View } from 'react-native';
import Animated, { runOnJS, useAnimatedStyle, useSharedValue, withTiming } from 'react-native-reanimated';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import { colors } from '../../theme/colors';
import { setPanMoved } from '../../utils/panGuard';

export interface Viewport {
  scale: number;
  tx: number;
  ty: number;
}

export interface ZoomPanHandle {
  resetToFit: () => void;
}

interface Props {
  viewportWidth: number;
  viewportHeight: number;
  worldWidth: number;
  worldHeight: number;
  minScale?: number;
  maxScale?: number;
  children: React.ReactNode;
  onViewportChange?: (v: Viewport) => void;
  gestureEnabled?: boolean;
  onTouchActive?: (active: boolean) => void;
}

export const ZoomPanCanvas = forwardRef<ZoomPanHandle, Props>(function ZoomPanCanvas(
  { viewportWidth, viewportHeight, worldWidth, worldHeight, minScale = 0.15, maxScale = 6, children, onViewportChange, gestureEnabled = true, onTouchActive },
  ref
) {
  const fitScale = Math.min(1, viewportWidth / Math.max(1, worldWidth), viewportHeight / Math.max(1, worldHeight)) || 1;
  const effMin = Math.min(minScale, fitScale);
  const centeredTx = (viewportWidth - worldWidth * fitScale) / 2;
  const centeredTy = (viewportHeight - worldHeight * fitScale) / 2;

  const scale = useSharedValue(fitScale);
  const tx = useSharedValue(centeredTx);
  const ty = useSharedValue(centeredTy);
  const startScale = useSharedValue(fitScale);
  const startTx = useSharedValue(0);
  const startTy = useSharedValue(0);
  const throttle = useSharedValue(0);

  const [, setTick] = useState(0);
  // Stop auto-following fitScale after the first interaction; shared values only read their initial value on mount.
  const hasInteractedRef = useRef(false);

  const clamp = (v: number, lo: number, hi: number) => {
    'worklet';
    return Math.min(hi, Math.max(lo, v));
  };

  function report() {
    onViewportChange?.({ scale: scale.value, tx: tx.value, ty: ty.value });
    setTick((t) => t + 1);
  }

  function reportThrottled() {
    'worklet';
    throttle.value += 1;
    if (throttle.value % 3 === 0) runOnJS(report)();
  }

  function markInteracted() {
    hasInteractedRef.current = true;
  }

  useEffect(() => {
    if (hasInteractedRef.current) return;
    scale.value = fitScale;
    tx.value = centeredTx;
    ty.value = centeredTy;
    report();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [fitScale, centeredTx, centeredTy]);

  useEffect(() => {
    if (!gestureEnabled) setPanMoved(false);
  }, [gestureEnabled]);

  const pan = Gesture.Pan()
    .enabled(gestureEnabled)
    .minPointers(1)
    .maxPointers(2)
    // Below a parent ScrollView's touch slop, so the canvas claims the drag before the page scrolls.
    .minDistance(2)
    .onBegin(() => {
      runOnJS(setPanMoved)(false);
      if (onTouchActive) runOnJS(onTouchActive)(true);
    })
    .onFinalize(() => {
      if (onTouchActive) runOnJS(onTouchActive)(false);
    })
    .onStart(() => {
      runOnJS(markInteracted)();
      runOnJS(setPanMoved)(true);
      startTx.value = tx.value;
      startTy.value = ty.value;
    })
    .onUpdate((e) => {
      tx.value = startTx.value + e.translationX;
      ty.value = startTy.value + e.translationY;
      reportThrottled();
    })
    .onEnd(() => runOnJS(report)());

  const pinch = Gesture.Pinch()
    .enabled(gestureEnabled)
    .onStart(() => {
      runOnJS(markInteracted)();
      startScale.value = scale.value;
      startTx.value = tx.value;
      startTy.value = ty.value;
    })
    .onUpdate((e) => {
      const next = clamp(startScale.value * e.scale, effMin, maxScale);
      const ratio = next / scale.value;
      tx.value = e.focalX - (e.focalX - tx.value) * ratio;
      ty.value = e.focalY - (e.focalY - ty.value) * ratio;
      scale.value = next;
      reportThrottled();
    })
    .onEnd(() => runOnJS(report)());

  const composed = Gesture.Simultaneous(pan, pinch);

  const containerRef = useRef<View>(null);

  function resetToFit() {
    hasInteractedRef.current = false;
    scale.value = withTiming(fitScale, { duration: 220 });
    tx.value = withTiming(centeredTx, { duration: 220 });
    ty.value = withTiming(centeredTy, { duration: 220 });
    setTimeout(report, 240);
  }

  useImperativeHandle(ref, () => ({ resetToFit }));

  useEffect(() => {
    if (Platform.OS !== 'web') return;
    const node = containerRef.current as unknown as HTMLElement | null;
    if (!node || typeof node.addEventListener !== 'function') return;

    const onWheel = (e: WheelEvent) => {
      if (!gestureEnabled) return;
      e.preventDefault();
      hasInteractedRef.current = true;
      const rect = node.getBoundingClientRect();
      const focalX = e.clientX - rect.left;
      const focalY = e.clientY - rect.top;
      const delta = -e.deltaY * 0.0016;
      const next = clamp(scale.value * (1 + delta), effMin, maxScale);
      const ratio = next / scale.value;
      tx.value = focalX - (focalX - tx.value) * ratio;
      ty.value = focalY - (focalY - ty.value) * ratio;
      scale.value = next;
      report();
    };
    const onKey = (e: KeyboardEvent) => {
      if (!gestureEnabled) return;
      if (e.key === '+' || e.key === '=') {
        hasInteractedRef.current = true;
        scale.value = clamp(scale.value * 1.15, effMin, maxScale);
        report();
      } else if (e.key === '-' || e.key === '_') {
        hasInteractedRef.current = true;
        scale.value = clamp(scale.value * 0.87, effMin, maxScale);
        report();
      } else if (e.key === '0') {
        resetToFit();
      }
    };
    node.addEventListener('wheel', onWheel, { passive: false });
    node.setAttribute('tabIndex', '0');
    node.addEventListener('keydown', onKey as any);
    return () => {
      node.removeEventListener('wheel', onWheel as any);
      node.removeEventListener('keydown', onKey as any);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [effMin, maxScale, worldWidth, worldHeight, viewportWidth, viewportHeight, gestureEnabled]);

  const animStyle = useAnimatedStyle(() => ({
    transform: [{ translateX: tx.value }, { translateY: ty.value }, { scale: scale.value }],
  }));

  return (
    <GestureDetector gesture={composed}>
    <View
      ref={containerRef}
      style={[styles.viewport, { width: viewportWidth, height: viewportHeight }]}
      onLayout={() => {
        if (hasInteractedRef.current) return;
        scale.value = fitScale;
        tx.value = centeredTx;
        ty.value = centeredTy;
        report();
      }}
      // @ts-ignore -- web-only cursor hint, harmless no-op on native
      accessible
    >
      <Animated.View style={[{ width: worldWidth, height: worldHeight, transformOrigin: '0 0' } as any, animStyle]}>
        {children}
      </Animated.View>
      <View style={styles.controls}>
        <Pressable
          style={styles.btn}
          onPress={() => {
            hasInteractedRef.current = true;
            scale.value = withTiming(clamp(scale.value * 1.3, effMin, maxScale), { duration: 150 });
            setTimeout(report, 160);
          }}
        >
          <Text style={styles.btnText}>+</Text>
        </Pressable>
        <Pressable
          style={styles.btn}
          onPress={() => {
            hasInteractedRef.current = true;
            scale.value = withTiming(clamp(scale.value * 0.77, effMin, maxScale), { duration: 150 });
            setTimeout(report, 160);
          }}
        >
          <Text style={styles.btnText}>-</Text>
        </Pressable>
        <Pressable style={styles.btn} onPress={resetToFit}>
          <Text style={styles.btnTextSmall}>FIT</Text>
        </Pressable>
      </View>
    </View>
    </GestureDetector>
  );
});

const styles = StyleSheet.create({
  viewport: { overflow: 'hidden', position: 'relative' },
  controls: { position: 'absolute', right: 8, bottom: 8, flexDirection: 'column', gap: 6, pointerEvents: 'box-none' },
  btn: {
    width: 34,
    height: 34,
    borderRadius: 8,
    backgroundColor: colors.surfaceRaised ?? '#20242c',
    borderWidth: 1,
    borderColor: colors.border ?? '#333a45',
    alignItems: 'center',
    justifyContent: 'center',
  },
  btnText: { color: colors.textPrimary, fontSize: 18, fontWeight: '700' },
  btnTextSmall: { color: colors.textPrimary, fontSize: 10, fontWeight: '700' },
});
