import { useCallback, useEffect, useRef, useState } from 'react';
import { getScheduleRuns } from '../services/schedules';
import type { ScheduleRun } from '../types/schedules';

export function useScheduleRuns(id: number, refreshKey: string) {
  const [runs, setRuns] = useState<ScheduleRun[]>([]);
  const [nextBefore, setNextBefore] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const pagedRef = useRef(false);
  const idRef = useRef(id);
  idRef.current = id;

  const loadFirst = useCallback(async () => {
    const requested = idRef.current;
    try {
      const page = await getScheduleRuns(requested, null);
      if (requested !== idRef.current) return;
      setRuns((prev) => {
        // keep already-paged older runs; replace the head with fresh data
        if (prev.length <= page.runs.length) return page.runs;
        const lastSeq = page.runs.length ? page.runs[page.runs.length - 1].seq : Infinity;
        return [...page.runs, ...prev.filter((r) => r.seq < lastSeq)];
      });
      if (!pagedRef.current) setNextBefore(page.next_before);
      setError(null);
    } catch (e) {
      if (requested === idRef.current) setError(e instanceof Error ? e.message : 'Failed to load runs');
    } finally {
      if (requested === idRef.current) setLoading(false);
    }
  }, []);

  useEffect(() => {
    pagedRef.current = false;
    setRuns([]);
    setNextBefore(null);
    setLoading(true);
  }, [id]);

  useEffect(() => {
    loadFirst();
  }, [id, refreshKey, loadFirst]);

  const loadMore = useCallback(async () => {
    if (nextBefore == null || loadingMore) return;
    setLoadingMore(true);
    try {
      pagedRef.current = true;
      const page = await getScheduleRuns(idRef.current, nextBefore);
      setRuns((prev) => [...prev, ...page.runs]);
      setNextBefore(page.next_before);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load runs');
    } finally {
      setLoadingMore(false);
    }
  }, [nextBefore, loadingMore]);

  return { runs, loading, loadingMore, error, hasMore: nextBefore != null, loadMore };
}
