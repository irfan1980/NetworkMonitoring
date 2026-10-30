import { useEffect, useRef, useState } from 'react';
import { useConnectionStore } from '../stores/connectionStore';
import { useHasIssues } from './useIssueSummary';

const HIDE_SIDEBAR_DELAY_MS = 4000;

export function useSidebarVisible(): boolean {
  const connectionState = useConnectionStore((s) => s.connectionState);
  const hasIssues = useHasIssues();
  const [showSidebar, setShowSidebar] = useState(true);
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    const shouldShow = connectionState !== 'live' || hasIssues;
    if (shouldShow) {
      if (hideTimer.current) {
        clearTimeout(hideTimer.current);
        hideTimer.current = null;
      }
      setShowSidebar(true);
    } else if (!hideTimer.current) {
      hideTimer.current = setTimeout(() => {
        hideTimer.current = null;
        setShowSidebar(false);
      }, HIDE_SIDEBAR_DELAY_MS);
    }
  }, [hasIssues, connectionState]);

  useEffect(
    () => () => {
      if (hideTimer.current) clearTimeout(hideTimer.current);
    },
    []
  );

  return showSidebar;
}
