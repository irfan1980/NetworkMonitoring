import React, { useCallback, useEffect, useRef, useState } from 'react';
import { PinPromptModal } from '../components/PinPromptModal';
import { RelayUnauthorizedError } from '../api/relayHttp';
import { ScheduleApiError } from '../services/schedules';

interface Pending {
  action: (pin: string) => Promise<unknown>;
  resolve: (v: unknown) => void;
  reject: (e: unknown) => void;
  subtitle?: string;
}

export interface PinSession {
  run: <T>(action: (pin: string) => Promise<T>, subtitle?: string) => Promise<T | undefined>;
  modal: React.ReactNode;
}

// The PIN is held in a ref for this host's lifetime only; it is never stored anywhere.
export function usePinSession(): PinSession {
  const pinRef = useRef<string | null>(null);
  const [pending, setPending] = useState<Pending | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [resetKey, setResetKey] = useState(0);
  const [busy, setBusy] = useState(false);
  const submittingRef = useRef(false);

  useEffect(() => {
    return () => {
      pinRef.current = null;
    };
  }, []);

  const prompt = useCallback((p: Pending, err: string | null) => {
    setError(err);
    setResetKey((k) => k + 1);
    setPending(p);
  }, []);

  const attempt = useCallback(
    async (p: Pending, pin: string): Promise<boolean> => {
      try {
        const value = await p.action(pin);
        pinRef.current = pin;
        p.resolve(value);
        return true;
      } catch (e) {
        if (e instanceof ScheduleApiError && e.status === 401 && e.code === 'invalid_pin') {
          pinRef.current = null;
          prompt(p, 'Wrong PIN. Try again.');
          return false;
        }
        if (e instanceof ScheduleApiError && e.status === 429) {
          pinRef.current = null;
          prompt(p, 'Too many attempts, wait a bit and try again.');
          return false;
        }
        if (e instanceof RelayUnauthorizedError) {
          prompt(p, "Your session expired. Sign in again.");
          return false;
        }
        p.reject(e);
        return true;
      }
    },
    [prompt]
  );

  const run = useCallback<PinSession['run']>(
    (action, subtitle) =>
      new Promise((resolve, reject) => {
        const p: Pending = { action, resolve: resolve as (v: unknown) => void, reject, subtitle };
        if (pinRef.current) {
          void attempt(p, pinRef.current).then((done) => {
            if (done) setPending(null);
          });
          return;
        }
        prompt(p, null);
      }),
    [attempt, prompt]
  );

  const modal = (
    <PinPromptModal
      visible={!!pending}
      title="Enter PIN"
      subtitle={pending?.subtitle ?? 'Required for schedule changes and test runs.'}
      errorText={error}
      resetKey={resetKey}
      busy={busy}
      onCancel={() => {
        pending?.resolve(undefined);
        setPending(null);
        setError(null);
      }}
      onSubmit={async (pin) => {
        if (!pending || submittingRef.current) return;
        submittingRef.current = true;
        setBusy(true);
        const done = await attempt(pending, pin);
        submittingRef.current = false;
        setBusy(false);
        if (done) {
          setPending(null);
          setError(null);
        }
      }}
    />
  );

  return { run, modal };
}
