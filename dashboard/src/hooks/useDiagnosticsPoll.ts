import { useCallback, useEffect, useRef, useState } from 'react';

const REFRESH_MS = 10_000;

interface PollState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
}

export function useDiagnosticsPoll<T>(fetcher: () => Promise<T>, active: boolean, key: string): PollState<T> {
  const [state, setState] = useState<PollState<T>>({ data: null, loading: true, error: null });
  const fetcherRef = useRef(fetcher);
  fetcherRef.current = fetcher;
  const keyRef = useRef(key);
  keyRef.current = key;

  const load = useCallback(
    async (isFirst: boolean, requestKey: string) => {
      if (isFirst) setState((s) => ({ ...s, loading: true, error: null }));
      try {
        const data = await fetcherRef.current();
        setState((s) => (requestKey === keyRef.current ? { data, loading: false, error: null } : s));
      } catch (e) {
        setState((s) =>
          requestKey === keyRef.current ? { data: s.data, loading: false, error: e instanceof Error ? e.message : 'Failed to load.' } : s
        );
      }
    },
    []
  );

  useEffect(() => {
    if (!active) return;
    setState({ data: null, loading: true, error: null });
    load(true, key);
    const id = setInterval(() => load(false, key), REFRESH_MS);
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, key]);

  return state;
}
