import { useState } from "react";
import { mediaSrc } from "../../lib/api";
import { formatDuration } from "../../lib/format";
import { StatusBadge } from "../Status";
import { cn } from "../../lib/utils";
import { ASPECT_CLASS } from "./media";
import { MediaLightbox } from "./MediaGrid";
import { formatCredits, modelShortName } from "../../lib/models";
import { providerLabel } from "../../lib/providers";

function SceneImage({ scene, label, aspectRatio, onExpand }) {
  const src = mediaSrc(scene.image_url);
  const portrait = aspectRatio === "9:16";
  return (
    <div
      className={cn(
        "shrink-0 overflow-hidden rounded-sm border border-line bg-canvas",
        ASPECT_CLASS[aspectRatio] || "aspect-video",
        portrait ? "w-20" : "w-40",
      )}
    >
      {src ? (
        <button
          type="button"
          className="block h-full w-full"
          aria-label={`Open ${label} image full size`}
          onClick={() => onExpand({ kind: "image", src, label, prompt: scene.image_prompt })}
        >
          <img src={src} alt="" className="h-full w-full object-cover" loading="lazy" />
        </button>
      ) : (
        <div className="flex h-full items-center justify-center text-xs text-fg-muted">No image yet</div>
      )}
    </div>
  );
}

export default function SceneList({ scenes, aspectRatio }) {
  const [expanded, setExpanded] = useState(null);
  if (!scenes.length) return null;
  return (
    <section aria-labelledby="scenes-heading">
      <h2 id="scenes-heading" className="mb-2 flex items-baseline gap-2 text-sm font-medium text-fg">
        Scenes
        <span className="font-mono text-xs font-normal text-fg-muted tabular">{scenes.length}</span>
      </h2>
      <ol className="divide-y divide-line rounded border border-line bg-surface">
        {scenes.map((scene, i) => {
          const n = (scene.index ?? scene.idx ?? i) + 1;
          const start = scene.start_sec ?? scene.start;
          const end = scene.end_sec ?? scene.end;
          const label = `Scene ${n}`;
          return (
            <li key={scene.index ?? scene.idx ?? i} className="flex flex-col gap-3 p-3 sm:flex-row">
              <SceneImage scene={scene} label={label} aspectRatio={aspectRatio} onExpand={setExpanded} />
              <div className="min-w-0 flex-1">
                <p className="flex items-baseline gap-2 text-sm font-medium text-fg">
                  {label}
                  {start != null && end != null && (
                    <span className="font-mono text-xs font-normal text-fg-muted tabular">
                      {formatDuration(start)} – {formatDuration(end)}
                    </span>
                  )}
                  {scene.status && scene.status !== "completed" && (
                    <StatusBadge status={scene.status} className="ml-auto text-xs font-normal" />
                  )}
                </p>
                <p className="mt-1 text-sm text-fg-secondary">{scene.text}</p>
                {scene.model && (
                  <p className="mt-1 flex flex-wrap gap-x-3 text-xs text-fg-muted">
                    <span>
                      {providerLabel(scene.service)} · {modelShortName(scene.model)}
                    </span>
                    {scene.credits != null && (
                      <span className="font-mono tabular">{formatCredits(scene.credits)} credits</span>
                    )}
                  </p>
                )}
              </div>
            </li>
          );
        })}
      </ol>
      <MediaLightbox expanded={expanded} onClose={() => setExpanded(null)} />
    </section>
  );
}
