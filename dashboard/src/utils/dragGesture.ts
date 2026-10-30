import { Platform } from 'react-native';

export interface DragPointCallbacks {
  onStart?: (clientX: number, clientY: number) => void;
  onMove: (clientX: number, clientY: number) => void;
  onEnd: () => void;
}

export function dragProps(cb: DragPointCallbacks): Record<string, unknown> {
  if (Platform.OS === 'web') {
    return {
      onPress: null,
      onPointerDown: (e: any) => {
        cb.onStart?.(e.clientX ?? 0, e.clientY ?? 0);
        // setPointerCapture can throw (e.g. synthetic pointers); optional chaining does not guard that.
        try {
          e.currentTarget?.setPointerCapture?.(e.pointerId);
        } catch {
        }
      },
      onPointerMove: (e: any) => {
        cb.onMove(e.clientX ?? 0, e.clientY ?? 0);
      },
      onPointerUp: (e: any) => {
        cb.onEnd();
        try {
          e.currentTarget?.releasePointerCapture?.(e.pointerId);
        } catch {
        }
      },
      onPointerCancel: () => cb.onEnd(),
      style: { cursor: 'grab', touchAction: 'none' },
    };
  }
  return {
    onStartShouldSetResponder: () => true,
    onMoveShouldSetResponder: () => true,
    onResponderGrant: (e: any) => cb.onStart?.(e.nativeEvent.pageX, e.nativeEvent.pageY),
    onResponderMove: (e: any) => cb.onMove(e.nativeEvent.pageX, e.nativeEvent.pageY),
    onResponderRelease: () => cb.onEnd(),
    onResponderTerminate: () => cb.onEnd(),
  };
}
