import { useCallback, useEffect, useRef, useState } from "react";

// Fetch a list endpoint once (and on reload), exposing honest loading/error state.
// `fetcher(signal)` resolves to { items, defaultId }.
export function useRemoteList(fetcher) {
  const [state, setState] = useState({ status: "loading", items: [], defaultId: null, error: null });
  const [attempt, setAttempt] = useState(0);
  const fetcherRef = useRef(fetcher);

  useEffect(() => {
    const controller = new AbortController();
    setState((s) => ({ ...s, status: "loading", error: null }));
    fetcherRef
      .current(controller.signal)
      .then((result) => setState({ status: "ok", ...result, error: null }))
      .catch((error) => {
        if (error?.name !== "AbortError") setState({ status: "error", items: [], defaultId: null, error });
      });
    return () => controller.abort();
  }, [attempt]);

  const reload = useCallback(() => setAttempt((a) => a + 1), []);
  return { ...state, reload };
}
