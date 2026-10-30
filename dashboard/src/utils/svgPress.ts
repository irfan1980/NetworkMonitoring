import { Platform } from 'react-native';
import { panMoved } from './panGuard';

type PressProps = Record<string, unknown>;

export function svgPressProps(onPress?: (() => void) | null): PressProps {
  if (!onPress) return {};
  if (Platform.OS === 'web') {
    return { onPress: null, onClick: () => { if (!panMoved()) onPress(); }, style: { cursor: 'pointer' } };
  }
  return { onPress: () => { if (!panMoved()) onPress(); } };
}

export function svgTapProps(onTap?: (() => void) | null, moveThreshold = 8): PressProps {
  if (!onTap) return {};
  if (Platform.OS === 'web') {
    let downX = 0;
    let downY = 0;
    return {
      onPress: null,
      onPointerDown: (e: { clientX?: number; clientY?: number }) => {
        downX = e.clientX ?? 0;
        downY = e.clientY ?? 0;
      },
      onClick: (e: { clientX?: number; clientY?: number }) => {
        const dx = (e.clientX ?? downX) - downX;
        const dy = (e.clientY ?? downY) - downY;
        if (Math.hypot(dx, dy) > moveThreshold) return;
        onTap();
      },
      style: { cursor: 'pointer' },
    };
  }
  return { onPress: () => { if (!panMoved()) onTap(); } };
}
