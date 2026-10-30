import { useCallback, useEffect, useState } from 'react';
import { createDowntime, deleteDowntime, listDowntime, updateDowntime } from '../services/downtime.service';
import type { DowntimeInput, DowntimeWindow } from '../types/downtime';

export function useDowntime() {
  const [windows, setWindows] = useState<DowntimeWindow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    try {
      setWindows(await listDowntime());
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not load downtime windows.');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  // Each mutation re-reads the list so every client converges on the relay's copy.
  const create = useCallback(
    async (input: DowntimeInput) => {
      await createDowntime(input);
      await reload();
    },
    [reload]
  );
  const update = useCallback(
    async (id: DowntimeWindow['id'], input: DowntimeInput) => {
      await updateDowntime(id, input);
      await reload();
    },
    [reload]
  );
  const remove = useCallback(
    async (id: DowntimeWindow['id']) => {
      await deleteDowntime(id);
      await reload();
    },
    [reload]
  );

  return { windows, loading, error, reload, create, update, remove };
}
