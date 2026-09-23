import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Poll `fetcher(signal)` on an adaptive interval.
 *
 * - `getInterval(data, error)` returns the delay in ms before the next poll,
 *   or null to stop polling (e.g. a run reached a terminal state).
 * - Requests never overlap: the next one is scheduled only after the previous
 *   settles. A refresh() during an in-flight request runs right after it.
 * - Pauses while the tab is hidden; refreshes immediately when it's visible again.
 * - Changing `key` (e.g. a route id) aborts the in-flight request, resets state
 *   and starts over, so stale responses are never applied.
 */
export function usePolling(fetcher, getInterval, key) {
  const [state, setState] = useState({ data: undefined, error: null, loading: true });
  const fetcherRef = useRef(fetcher);
  const intervalRef = useRef(getInterval);
  const refreshRef = useRef(() => {});

  useEffect(() => {
    fetcherRef.current = fetcher;
    intervalRef.current = getInterval;
  });

  useEffect(() => {
    let cancelled = false;
    let timer = null;
    let controller = null;
    let inFlight = false;
    let refreshQueued = false;
    let lastData;
    let lastError = null;

    setState({ data: undefined, error: null, loading: true });

    const schedule = () => {
      clearTimeout(timer);
      if (cancelled || document.hidden) return;
      const ms = intervalRef.current(lastData, lastError);
      if (ms == null) return;
      timer = setTimeout(tick, ms);
    };

    const tick = async () => {
      if (cancelled) return;
      if (inFlight) {
        refreshQueued = true;
        return;
      }
      clearTimeout(timer);
      inFlight = true;
      controller = new AbortController();
      try {
        const data = await fetcherRef.current(controller.signal);
        if (cancelled) return;
        lastData = data;
        lastError = null;
        setState({ data, error: null, loading: false });
      } catch (err) {
        if (cancelled || err?.name === "AbortError") return;
        lastError = err;
        setState((s) => ({ data: s.data, error: err, loading: false }));
      } finally {
        inFlight = false;
        if (!cancelled) {
          if (refreshQueued) {
            refreshQueued = false;
            tick();
          } else {
            schedule();
          }
        }
      }
    };

    const onVisibility = () => {
      if (document.hidden) clearTimeout(timer);
      else tick();
    };

    refreshRef.current = tick;
    document.addEventListener("visibilitychange", onVisibility);
    tick();

    return () => {
      cancelled = true;
      clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [key]);

  const refresh = useCallback(() => refreshRef.current(), []);
  return { ...state, refresh };
}
