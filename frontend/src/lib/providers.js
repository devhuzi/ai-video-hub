// Provider names and the env var each one needs. The backend's /api/setup
// response is authoritative; these are fallbacks while it loads.

export const PROVIDER_LABELS = { snapgen: "SnapGen", kie: "Kie.ai", fal: "fal.ai", openrouter: "OpenRouter" };

export const PROVIDER_ENV = {
  snapgen: "SNAPGEN_API_KEY",
  kie: "KIE_API_KEY",
  fal: "FAL_KEY",
  openrouter: "OPENROUTER_API_KEY",
};

export const IMAGE_PROVIDERS = ["snapgen", "kie", "fal"];

// Values saved by older versions of the app (localStorage settings).
const LEGACY = { geminigen: "snapgen", straico: "fal", "kie.ai": "kie" };

export function normalizeProvider(value, fallback = "snapgen") {
  const v = LEGACY[value] || value;
  return IMAGE_PROVIDERS.includes(v) ? v : fallback;
}

// Display name for a provider id, including ids saved by older backends.
export function providerLabel(value) {
  if (!value) return null;
  const v = LEGACY[value] || value;
  return PROVIDER_LABELS[v] || value;
}

export function formatUsd(n) {
  if (typeof n !== "number" || !Number.isFinite(n)) return null;
  if (n === 0) return "$0";
  return n < 0.1 ? `$${n.toFixed(3)}` : `$${n.toFixed(2)}`;
}
