import { useEffect, useState } from "react";
import { api, errorMessage } from "../lib/api";
import { formatUsd } from "../lib/providers";

const plural = (n, word) => `${n} ${word}${n === 1 ? "" : "s"}`;

/**
 * Cost/usage estimate from the backend. Only shows numbers the backend
 * returns — a USD figure appears only when the backend could price it.
 * `query` is null while the form isn't ready to estimate.
 */
export default function Estimate({ query }) {
  const key = query ? JSON.stringify(query) : null;
  const [state, setState] = useState({ key: null, data: null, error: null });

  useEffect(() => {
    if (!key) return undefined;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      api
        .getEstimate(JSON.parse(key), controller.signal)
        .then((data) => setState({ key, data, error: null }))
        .catch((error) => {
          if (error?.name !== "AbortError") setState({ key, data: null, error });
        });
    }, 350);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [key]);

  let content;
  if (!key) {
    content = <p className="text-sm text-fg-muted">Available once the form is complete.</p>;
  } else if (state.key !== key) {
    content = <p className="text-sm text-fg-muted">Calculating…</p>;
  } else if (state.error) {
    content = <p className="text-sm text-fg-secondary">Estimate unavailable: {errorMessage(state.error)}</p>;
  } else {
    const g = state.data?.generations || {};
    const parts = [];
    if (g.images != null) parts.push(plural(g.images, "image generation"));
    if (g.videos != null) parts.push(plural(g.videos, "video generation"));
    if (g.tts) parts.push(plural(g.tts, "narration request"));
    const usd = formatUsd(state.data?.estimated_usd);
    content = (
      <>
        <p className="text-sm text-fg">{parts.length ? parts.join(", ") : "No generations reported."}</p>
        {usd && (
          <p className="mt-1 text-sm text-fg-secondary">
            Priced items: about <span className="font-mono text-fg tabular">{usd}</span>
          </p>
        )}
        {state.data?.notes?.length > 0 && (
          <ul className="mt-2 space-y-1 text-xs text-fg-muted">
            {state.data.notes.map((n, i) => (
              <li key={i}>{n}</li>
            ))}
          </ul>
        )}
      </>
    );
  }

  return (
    <section aria-labelledby="estimate-heading" aria-live="polite">
      <h2 id="estimate-heading" className="mb-1.5 text-sm font-medium text-fg">
        Estimate
      </h2>
      {content}
    </section>
  );
}
