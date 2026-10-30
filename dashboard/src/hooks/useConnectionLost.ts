import { useEffect, useState } from 'react';
import { useConnectionStore } from '../stores/connectionStore';

export const CONNECTION_GRACE_MS = 2000;

export interface ConnectionLoss {
  visible: boolean;
  downSinceMs: number | null;
  attempts: number;
  nextRetryAtMs: number | null;
  everConnected: boolean;
}

export function useConnectionLost(): ConnectionLoss {
  const connectionState = useConnectionStore((s) => s.connectionState);
  const connectionDownSinceMs = useConnectionStore((s) => s.connectionDownSinceMs);
  const reconnectAttempts = useConnectionStore((s) => s.reconnectAttempts);
  const nextRetryAtMs = useConnectionStore((s) => s.nextRetryAtMs);
  const everConnected = useConnectionStore((s) => s.everConnected);

  const [graceElapsed, setGraceElapsed] = useState(false);

  useEffect(() => {
    if (connectionState === 'live' || connectionState === 'unauthorized' || connectionDownSinceMs == null) {
      setGraceElapsed(false);
      return;
    }
    const remaining = connectionDownSinceMs + CONNECTION_GRACE_MS - Date.now();
    if (remaining <= 0) {
      setGraceElapsed(true);
      return;
    }
    setGraceElapsed(false);
    const t = setTimeout(() => setGraceElapsed(true), remaining);
    return () => clearTimeout(t);
  }, [connectionState, connectionDownSinceMs]);

  return {
    visible:
      connectionState !== 'live' &&
      connectionState !== 'unauthorized' &&
      connectionDownSinceMs != null &&
      graceElapsed,
    downSinceMs: connectionDownSinceMs,
    attempts: reconnectAttempts,
    nextRetryAtMs,
    everConnected,
  };
}
