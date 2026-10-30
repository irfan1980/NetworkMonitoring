import { useEffect, useRef, useState } from 'react';
import { previewTrigger, ScheduleApiError } from '../services/schedules';
import type { FieldError, PreviewResult, Trigger } from '../types/schedules';

const DEBOUNCE_MS = 450;

interface PreviewState {
  result: PreviewResult | null;
  errors: FieldError[];
  message: string | null;
  loading: boolean;
}

const EMPTY: PreviewState = { result: null, errors: [], message: null, loading: false };

export function useTriggerPreview(trigger: Trigger | null): PreviewState {
  const [state, setState] = useState<PreviewState>(EMPTY);
  const seq = useRef(0);
  const key = trigger ? JSON.stringify(trigger) : '';

  useEffect(() => {
    if (!trigger) {
      setState(EMPTY);
      return;
    }
    const mine = ++seq.current;
    setState((s) => ({ ...s, loading: true }));
    const t = setTimeout(async () => {
      try {
        const result = await previewTrigger(trigger);
        if (mine === seq.current) setState({ result, errors: [], message: null, loading: false });
      } catch (e) {
        if (mine !== seq.current) return;
        if (e instanceof ScheduleApiError) {
          setState({ result: null, errors: e.fieldErrors, message: e.fieldErrors.length ? null : e.message, loading: false });
        } else {
          setState({ result: null, errors: [], message: e instanceof Error ? e.message : 'Preview failed', loading: false });
        }
      }
    }, DEBOUNCE_MS);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  return state;
}
