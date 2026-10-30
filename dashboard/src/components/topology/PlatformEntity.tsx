import React, { useEffect } from 'react';
import { Circle, G, Text as SvgText } from 'react-native-svg';
import Animated, {
  cancelAnimation,
  useAnimatedProps,
  useSharedValue,
  withRepeat,
  withTiming,
  Easing,
} from 'react-native-reanimated';
import { colors, platformTypeAccent, platformTypeAccentDefault, platformTypeCode } from '../../theme/colors';
import { FONT_FAMILY } from '../../theme/typography';
import { svgPressProps } from '../../utils/svgPress';

const AnimatedCircleC = Animated.createAnimatedComponent(Circle);

interface Props {
  cx: number;
  cy: number;
  r: number;
  label: string;
  state: 'ok' | 'grey' | 'down' | 'yellow' | 'degraded' | 'red';
  type?: string;
  hasIssues?: boolean;
  subLabel?: string;
  onPress?: () => void;
  dragHandleProps?: Record<string, unknown>;
  pulse?: boolean;
}

const STATE_COLOR = {
  ok: colors.good,
  grey: colors.unknown,
  down: colors.bad,
  yellow: colors.warn,
  degraded: colors.degraded,
  red: colors.bad,
} as const;

function PlatformEntityImpl({ cx, cy, r, label, state, type, hasIssues, subLabel, onPress, dragHandleProps, pulse: pulseEnabled = true }: Props) {
  const ringColor = STATE_COLOR[state];
  const typeAccent = type ? (platformTypeAccent[type] ?? platformTypeAccentDefault) : null;
  const typeCode = type ? (platformTypeCode[type] ?? type.slice(0, 2).toUpperCase()) : null;
  const pulse = useSharedValue(0);

  useEffect(() => {
    if (state === 'ok' && pulseEnabled) {
      pulse.value = withRepeat(withTiming(1, { duration: 1800, easing: Easing.inOut(Easing.ease) }), -1, true);
    } else {
      cancelAnimation(pulse);
      pulse.value = withTiming(0, { duration: 200 });
    }
    return () => cancelAnimation(pulse);
  }, [pulse, state, pulseEnabled]);

  const ringProps = useAnimatedProps(() => ({
    r: r + 5 + pulse.value * 7,
    opacity: 0.45 - pulse.value * 0.3,
  }));

  return (
    <G {...(dragHandleProps ?? svgPressProps(onPress))}>
      <AnimatedCircleC cx={cx} cy={cy} fill="none" stroke={ringColor} strokeWidth={1.5} animatedProps={ringProps} />
      <Circle cx={cx} cy={cy} r={r} fill={colors.surface} stroke={ringColor} strokeWidth={5} />

      <SvgText
        x={cx}
        y={cy + Math.max(16, r * 0.24) * 0.32 - (typeAccent ? Math.max(9, r * 0.13) : 0)}
        textAnchor="middle"
        fill={colors.textPrimary}
        fontSize={Math.max(16, r * 0.24)}
        fontWeight="900"
        fontFamily={FONT_FAMILY}
      >
        {label}
      </SvgText>

      {subLabel ? (
        <SvgText
          x={cx}
          y={cy + r + 16}
          textAnchor="middle"
          fill={colors.textMuted}
          fontSize={Math.max(9, r * 0.12)}
          fontWeight="600"
          fontFamily={FONT_FAMILY}
        >
          {subLabel}
        </SvgText>
      ) : null}

      {typeAccent && typeCode ? (
        <G>
          <Circle cx={cx} cy={cy + Math.max(12, r * 0.18)} r={Math.max(9, r * 0.13)} fill={typeAccent} opacity={0.85} />
          <SvgText
            x={cx}
            y={cy + Math.max(12, r * 0.18) + Math.max(9, r * 0.13) * 0.32}
            textAnchor="middle"
            fill={colors.background}
            fontSize={Math.max(8, r * 0.1)}
            fontWeight="900"
            fontFamily={FONT_FAMILY}
          >
            {typeCode}
          </SvgText>
        </G>
      ) : null}

      {hasIssues ? (
        <G>
          <Circle cx={cx + r * 0.72} cy={cy - r * 0.72} r={Math.max(9, r * 0.14)} fill={colors.warn} stroke={colors.background} strokeWidth={2} />
          <SvgText
            x={cx + r * 0.72}
            y={cy - r * 0.72 + Math.max(9, r * 0.14) * 0.34}
            textAnchor="middle"
            fill={colors.background}
            fontSize={Math.max(10, r * 0.15)}
            fontWeight="900"
            fontFamily={FONT_FAMILY}
          >
            !
          </SvgText>
        </G>
      ) : null}
    </G>
  );
}

export const PlatformEntity = React.memo(PlatformEntityImpl);
