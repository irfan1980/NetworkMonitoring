import { Platform } from 'react-native';

// SVG <Text> does not inherit the RN font and falls back to serif on web; always set this.
export const FONT_FAMILY = Platform.select({
  web: 'system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif',
  ios: 'System',
  android: 'sans-serif',
  default: 'sans-serif',
}) as string;
