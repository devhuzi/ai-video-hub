// Image / video model catalogues from the backend (/api/images/models,
// /api/video/models) turned into select options, plus the rules that keep a
// video selection valid for its model. Shared by New pack, Script Studio and
// the retry / regenerate dialogs.
import { PROVIDER_LABELS, formatUsd } from "./providers";

export const DEFAULT_VIDEO_MODEL = "veo-3.1-fast";
export const LADDER_HINT = "If it fails, the next configured provider is tried: SnapGen → Kie.ai → fal.ai.";
export const CREDITS_NOTE = "Credits as listed by SnapGen — may change.";

export function formatCredits(n) {
  if (typeof n !== "number" || !Number.isFinite(n)) return null;
  return n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

// "snapgen/grok-image" → "grok-image"; fal ids are shown whole.
export function modelShortName(id) {
  if (!id) return null;
  if (MODEL_NAMES[id]) return MODEL_NAMES[id];
  const [prefix, rest] = id.split(/\/(.*)/);
  return (prefix === "snapgen" || prefix === "kie") && rest ? rest : id;
}

// Readable names for stored model ids (run details, cards). Unknown fal ids fall back to the id.
const MODEL_NAMES = {
  "snapgen/nano-banana-2": "Nano Banana 2", "snapgen/nano-banana-pro": "Nano Banana Pro",
  "snapgen/nano-banana-2-lite": "Nano Banana 2 Lite", "snapgen/grok-image": "Grok Image",
  "snapgen/gpt-image-2": "GPT Image 2", "kie/nano-banana-2": "Nano Banana 2",
  "veo-3.1-fast": "Veo 3.1 Fast", "veo-3.1-lite": "Veo 3.1 Lite", "veo-3.1": "Veo 3.1",
  "omni-flash": "Omni Flash", "vela": "Vela AI",
};

export function modelLabel(id) {
  if (!id) return null;
  const name = MODEL_NAMES[id];
  if (id.startsWith("snapgen/")) return `SnapGen · ${name || modelShortName(id)}`;
  if (id.startsWith("kie/")) return `Kie.ai · ${name || modelShortName(id)}`;
  if (id.startsWith("fal-ai/")) return `fal.ai · ${id.slice("fal-ai/".length)}`;
  return name || id;
}

const imageUsable = (m) => m.configured !== false;

// Grouped by provider (the backend lists them in ladder order). Unconfigured
// providers stay visible but disabled, with the env var to add.
export function imageModelOptions(models, setup) {
  return models.map((m) => {
    const price = formatUsd(m.price_usd);
    const cost = m.credits_label || (price ? `${price} / image` : null);
    const usable = imageUsable(m);
    return {
      value: m.id,
      label: `${m.name || m.id}${cost ? ` · ${cost}` : ""}`,
      group: PROVIDER_LABELS[m.provider] || m.provider,
      disabled: !usable,
      suffix: usable ? null : `add ${setup.envFor(m.provider)}`,
    };
  });
}

// `id` if the catalogue offers it and its provider is configured, else the
// preferred default, else the first usable model. null while nothing is usable.
export function usableImageModel(models, id, preferredId) {
  const find = (x) => models.find((m) => m.id === x && imageUsable(m));
  const hit = find(id) || find(preferredId) || models.find(imageUsable);
  return hit ? hit.id : null;
}

// The catalogue entry the settings should use: the chosen model, unless it's
// unknown or can't render this pack (extend shape, aspect ratio) — then the default.
export function effectiveVideoModel(models, defaultId, id, { isExtend = false, aspect = null } = {}) {
  const usable = (m) => m && (!isExtend || m.supports_extend) && (!aspect || m.aspects.includes(aspect));
  const chosen = models.find((m) => m.id === id);
  if (usable(chosen)) return chosen;
  const fallback = models.find((m) => m.id === (defaultId || DEFAULT_VIDEO_MODEL));
  return (usable(fallback) && fallback) || models.find(usable) || null;
}

// Snap values the model doesn't accept to its defaults. Returns `s` unchanged
// when valid. `lockAspect` keeps the aspect ratio (an existing run's is fixed).
export function snapVideoSettings(s, model, { lockAspect = false } = {}) {
  const next = { ...s, videoModel: model.id };
  if (model.resolutions.length && !model.resolutions.includes(s.videoResolution)) {
    next.videoResolution = model.default_resolution;
  }
  if (!model.durations.includes(Number(s.videoDuration))) next.videoDuration = model.default_duration;
  if (!lockAspect && s.aspectRatio && !model.aspects.includes(s.aspectRatio)) next.aspectRatio = model.aspects[0];
  const changed = Object.keys(next).some((k) => next[k] !== s[k]);
  return changed ? next : s;
}

// Video models as options; ones that can't render this pack are disabled with the reason.
export function videoModelOptions(models, { isExtend = false, aspect = null } = {}) {
  return models.map((m) => {
    let reason = null;
    if (isExtend && !m.supports_extend) reason = "Not available for extend packs";
    else if (aspect && !m.aspects.includes(aspect)) reason = `no ${aspect}`;
    return {
      value: m.id,
      label: `${m.label}${m.credits_label ? ` · ${m.credits_label}` : ""}`,
      disabled: !!reason,
      suffix: reason,
    };
  });
}

export function videoModelHint(model, mode) {
  if (!model) return null;
  if (mode === "extend") {
    return "Clip 1 starts from the image; each later clip extends the previous one on Veo. Omni Flash and Vela AI are not available for extend packs.";
  }
  if (model.image_mode === "ingredient") {
    return "Omni Flash uses the images as references, not exact first/last frames.";
  }
  if (model.image_mode === "start_image") {
    return "Experimental: Vela AI isn't in SnapGen's public API docs, so SnapGen may reject it. Each clip uses only its first image.";
  }
  return mode === "frame" ? "Each clip runs from its first image to its last image." : null;
}

export const durationOptions = (model, fallback) =>
  (model?.durations || [fallback]).map((d) => ({ value: String(d), label: `${d} seconds` }));

export const resolutionOptions = (model) => (model?.resolutions || []).map((r) => ({ value: r, label: r }));
