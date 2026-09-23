import { useEffect, useState } from "react";
import { api } from "../lib/api";
import { formatCredits } from "../lib/models";
import { PROVIDER_LABELS } from "../lib/providers";

/**
 * Remaining provider credits, e.g. "SnapGen: 1,234 credits". Providers whose
 * balance is unknown (unconfigured, or the lookup failed) are left out; the
 * whole line is hidden when none is known.
 */
export default function Balances() {
  const [data, setData] = useState(null);

  useEffect(() => {
    const controller = new AbortController();
    api
      .getBalances(controller.signal)
      .then(setData)
      .catch(() => {}); // informational only
    return () => controller.abort();
  }, []);

  const known = Object.entries(data || {}).filter(([, v]) => typeof v === "number");
  if (!known.length) return null;
  return (
    <p className="text-xs text-fg-muted">
      Balance:{" "}
      {known.map(([provider, credits], i) => (
        <span key={provider}>
          {i > 0 && " · "}
          {PROVIDER_LABELS[provider] || provider}: <span className="font-mono text-fg-secondary tabular">{formatCredits(credits)}</span> credits
        </span>
      ))}
    </p>
  );
}
