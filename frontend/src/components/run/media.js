// Shared helpers for run media (images, clips, scenes).

export const ASPECT_CLASS = {
  "16:9": "aspect-video",
  "9:16": "aspect-[9/16]",
  "1:1": "aspect-square",
};

export const GRID_CLASS = {
  "16:9": "grid-cols-1 sm:grid-cols-2 2xl:grid-cols-3",
  "9:16": "grid-cols-2 sm:grid-cols-3 xl:grid-cols-4 2xl:grid-cols-5",
  "1:1": "grid-cols-2 sm:grid-cols-3 2xl:grid-cols-4",
};

// Normalise image/video rows across backend versions.
export function mediaItems(pipeline, kind) {
  const rows = pipeline[`${kind}s`] || pipeline[`${kind}_details`];
  if (Array.isArray(rows) && rows.length) {
    return [...rows].sort((a, b) => (a.index ?? 0) - (b.index ?? 0));
  }
  const prompts = pipeline[`${kind}_prompts`] || [];
  const urls = pipeline[`${kind}_urls`] || [];
  return prompts.map((prompt, index) => ({
    index,
    prompt,
    url: urls[index] || null,
    status: urls[index] ? "completed" : "pending",
  }));
}

// Frame indexes from the backend are 0-based; labels are 1-based.
export function frameLabel(video, videoSystem) {
  const a = video.first_frame_index;
  const b = video.last_frame_index;
  if (a == null && b == null) {
    if (videoSystem === "grok_sequential_extend" && video.index > 0) return `Extends clip ${video.index}`;
    return null;
  }
  if (b == null) return `Extends from image ${a + 1}`;
  if (a == null) return `Ends on image ${b + 1}`;
  if (a === b) return `Holds on image ${a + 1}`;
  return `Frames ${a + 1} → ${b + 1}`;
}
