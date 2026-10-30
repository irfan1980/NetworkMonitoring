import { useEffect, useRef } from 'react';
import { Platform } from 'react-native';
import { useConnectionStore } from '../stores/connectionStore';

export function useAlarm() {
  const activeAlerts = useConnectionStore((s) => s.activeAlerts);
  const silencedAlertIds = useConnectionStore((s) => s.silencedAlertIds);

  const ctxRef = useRef<AudioContext | null>(null);
  const oscRef = useRef<OscillatorNode | null>(null);
  const gainRef = useRef<GainNode | null>(null);

  useEffect(() => {
    if (Platform.OS !== 'web') return;

    function resume() {
      if (!ctxRef.current) {
        const Ctx = (window as unknown as { AudioContext: typeof AudioContext }).AudioContext;
        if (Ctx) ctxRef.current = new Ctx();
      }
      ctxRef.current?.resume?.();
    }
    window.addEventListener('pointerdown', resume);
    window.addEventListener('keydown', resume);
    resume();

    return () => {
      window.removeEventListener('pointerdown', resume);
      window.removeEventListener('keydown', resume);
    };
  }, []);

  useEffect(() => {
    if (Platform.OS !== 'web') return;

    const now = Date.now();
    const candidates = Object.values(activeAlerts).filter((a) => {
      if (a.acknowledged) return false;
      if (!a.channels.includes('alarm')) return false;
      const silencedUntil = silencedAlertIds[a.alertId];
      if (silencedUntil && silencedUntil > now) return false;
      return true;
    });

    if (candidates.length === 0) {
      oscRef.current?.stop();
      oscRef.current?.disconnect();
      gainRef.current?.disconnect();
      oscRef.current = null;
      gainRef.current = null;
      return;
    }

    const top = candidates.sort((a, b) => a.severity - b.severity)[0];
    const freq = top.severity <= 1 ? 880 : top.severity === 2 ? 660 : 440;

    if (!ctxRef.current) return;

    if (!oscRef.current) {
      const ctx = ctxRef.current;
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      gain.gain.value = 0.06;
      osc.connect(gain).connect(ctx.destination);
      osc.start();
      oscRef.current = osc;
      gainRef.current = gain;
    }
    oscRef.current.frequency.setValueAtTime(freq, ctxRef.current.currentTime);
  }, [activeAlerts, silencedAlertIds]);
}
