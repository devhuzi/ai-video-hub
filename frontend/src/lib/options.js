// Option lists shared by the editor pages.

export const ASPECT_LABELS = {
  "16:9": "16:9 · Landscape",
  "9:16": "9:16 · Portrait",
  "1:1": "1:1 · Square",
};

export const ASPECT_OPTIONS = ["16:9", "9:16"].map((value) => ({ value, label: ASPECT_LABELS[value] }));

// `value` if it's one of `options` (compared as strings), otherwise `fallback`.
export function oneOf(value, options, fallback) {
  return options.some((o) => o.value === String(value)) ? value : fallback;
}
