export const colors = {
  background: '#0d1117',
  surface: '#161b22',
  surfaceRaised: '#1c2330',
  border: '#2a3140',

  textPrimary: '#e6edf3',
  textSecondary: '#9aa7b8',
  textMuted: '#6b7686',

  good: '#3fd97a',
  goodDim: 'rgba(63, 217, 122, 0.15)',
  warn: '#f5c542',
  warnDim: 'rgba(245, 197, 66, 0.15)',
  bad: '#ff4d4f',
  badDim: 'rgba(255, 77, 79, 0.15)',
  degraded: '#ff8c42',
  degradedDim: 'rgba(255, 140, 66, 0.18)',
  unknown: '#8a94a6',
  unknownDim: 'rgba(138, 148, 166, 0.18)',
  blocked: '#4a90d9',
  blockedDim: 'rgba(74, 144, 217, 0.15)',

  brokenEnd: '#b5555f',

  accent: '#4a90d9',
  packet: '#4ad9d9',

  glowGood: '#3fd97a',
  glowBad: '#ff4d4f',
  glowWarn: '#f5c542',
  glowGrey: '#6b7686',
} as const;

export type Colors = typeof colors;

export const platformTypeAccent: Record<string, string> = {
  mysql: '#4a90d9',
  postgres: '#a970e0',
  mssql: '#e0a13f',
};
export const platformTypeAccentDefault = '#8a94a6';

export const platformTypeCode: Record<string, string> = {
  mysql: 'MY',
  postgres: 'PG',
  mssql: 'MS',
};
