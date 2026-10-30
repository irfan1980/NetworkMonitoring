import { useWindowDimensions } from 'react-native';

export const BREAKPOINTS = { xs: 320, sm: 640, md: 768, lg: 1024, xl: 1280 };

export function useResponsive() {
  const { width, height } = useWindowDimensions();
  return {
    width,
    height,
    isXs: width < BREAKPOINTS.sm,
    isSm: width >= BREAKPOINTS.sm && width < BREAKPOINTS.md,
    isMd: width >= BREAKPOINTS.md && width < BREAKPOINTS.lg,
    isLg: width >= BREAKPOINTS.lg && width < BREAKPOINTS.xl,
    isXl: width >= BREAKPOINTS.xl,
    isPhone: width < BREAKPOINTS.md,
    isWall: width >= 1600,
  };
}
