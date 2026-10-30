import { useCallback, useEffect, useRef, useState } from 'react';
import { getSchedule, ScheduleApiError } from '../services/schedules';
import type { ScheduleDetail } from '../types/schedules';

export function useScheduleDetail(id: number, refreshKey: string) {
  const [detail, setDetail] = useState<ScheduleDetail | null>(null);
  const [error, setError] = useState<ScheduleApiError | Error | null>(null);
  const [loading, setLoading] = useState(true);
  const idRef = useRef(id);
  idRef.current = id;

  const reload = useCallback(async () => {
    const requested = idRef.current;
    try {
      const d = await getSchedule(requested);
      if (requested === idRef.current) {
        setDetail(d);
        setError(null);
      }
    } catch (e) {
      if (requested === idRef.current) setError(e instanceof Error ? e : new Error('Failed to load schedule'));
    } finally {
      if (requested === idRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    setLoading(true);
    setDetail(null);
    reload();
  }, [id, reload]);

  useEffect(() => {
    reload();
    // refreshKey changes whenever the live row changes (state, last run, running)
  }, [refreshKey, reload]);

  return { detail, error, loading, reload, setDetail };
}
