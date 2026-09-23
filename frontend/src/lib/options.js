// Option lists shared by the editor pages.

export const ASPECT_OPTIONS = [
  { value: "16:9", label: "16:9 · Landscape" },
  { value: "9:16", label: "9:16 · Portrait" },
];

// `value` if it's one of `options` (compared as strings), otherwise `fallback`.
export function oneOf(value, options, fallback) {
  return options.some((o) => o.value === String(value)) ? value : fallback;
}
