import { useCallback, useEffect, useState } from 'react';
import { getPushWindow, setPushWindow } from '../services/downtime.service';
import type { AccountWindow } from '../types/downtime';

export function usePushWindow() {
  const [window, setWindow] = useState<AccountWindow | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setWindow(await getPushWindow());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load the push window.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const save = useCallback(
    async (next: AccountWindow | null) => {
      await setPushWindow(next);
      await reload();
    },
    [reload]
  );

  return { window, loading, error, save };
}
