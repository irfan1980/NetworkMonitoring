import { useEffect, useRef, useState } from 'react';

export function useRollingBuffer(value: number | null, maxLen = 40): number[] {
  const [buffer, setBuffer] = useState<number[]>([]);
  const lastValue = useRef<number | null>(null);

  useEffect(() => {
    if (value == null || value === lastValue.current) return;
    lastValue.current = value;
    setBuffer((prev) => {
      const next = [...prev, value];
      return next.length > maxLen ? next.slice(next.length - maxLen) : next;
    });
  }, [value, maxLen]);

  return buffer;
}
