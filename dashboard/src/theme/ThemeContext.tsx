import React, { createContext, useContext, useMemo } from 'react';
import { StyleSheet } from 'react-native';
import { colors, type Colors } from './colors';

interface ThemeValue {
  colors: Colors;
}

const ThemeContext = createContext<ThemeValue>({ colors });

export function ThemeProvider({ children }: { children: React.ReactNode }) {
  const value = useMemo<ThemeValue>(() => ({ colors }), []);
  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  return useContext(ThemeContext);
}

export function createThemedStyles<T extends StyleSheet.NamedStyles<T>>(
  factory: (c: Colors) => T
) {
  const cached = StyleSheet.create(factory(colors));
  return function useStyles(): T {
    return cached;
  };
}
