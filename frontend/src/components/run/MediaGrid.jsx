import { useState } from "react";
import { toast } from "sonner";
import { Maximize2, RefreshCw } from "lucide-react";
import { StatusBadge } from "../Status";
import { Button } from "../ui/button";
import { ConfirmDialog, Lightbox } from "../ui/dialog";
import { api, errorMessage, mediaSrc } from "../../lib/api";
import { cn } from "../../lib/utils";
import { ASPECT_CLASS, GRID_CLASS, frameLabel } from "./media";
import { providerLabel } from "../../lib/providers";
import { SelectField } from "../ui/select";
import { useRemoteList } from "../../hooks/useRemoteList";
import { useSetup } from "../../hooks/useSetup";
import { formatCredits, imageModelOptions, modelShortName, videoModelOptions } from "../../lib/models";

function MediaCard({ item, kind, aspectRatio, videoSystem, canRegenerate, onRegenerate, onExpand }) {
  const src = mediaSrc(item.url);
  const n = (item.index ?? 0) + 1;
  const label = `${kind === "image" ? "Image" : "Video"} ${n}`;
  const frames = kind === "video" ? frameLabel(item, videoSystem) : null;
  const failed = item.status === "failed";

  return (
    <li className={cn("flex flex-col rounded border bg-surface", failed ? "border-status-failed/50" : "border-line")}>
      <div className="flex items-center justify-between gap-2 px-3 py-2">
        <span className="text-sm font-medium text-fg">{label}</span>
        <StatusBadge status={item.status || "pending"} className="text-xs" />
      </div>
      <div className={cn("relative w-full overflow-hidden bg-canvas", ASPECT_CLASS[aspectRatio] || "aspect-video")}>
        {src ? (
          kind === "image" ? (
            <button
              type="button"
              onClick={() => onExpand({ item, kind, src, label })}
              className="block h-full w-full"
              aria-label={`Open ${label} full size`}
            >
              <img src={src} alt={label} className="h-full w-full object-cover" loading="lazy" />
            </button>
          ) : (
            <>
              <video src={src} controls preload="metadata" className="h-full w-full object-contain" aria-label={label} />
              <Button
                variant="secondary"
                size="icon-sm"
                className="absolute right-2 top-2"
                aria-label={`Open ${label} full size`}
                onClick={() => onExpand({ item, kind, src, label })}
              >
                <Maximize2 className="h-3.5 w-3.5" aria-hidden="true" />
              </Button>
            </>
          )
        ) : (
          <div className="flex h-full items-center justify-center px-3 text-center text-xs text-fg-muted">
            {item.status === "completed" ? "No preview available" : null}
          </div>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-1.5 px-3 py-2">
        {(frames || item.service) && (
          <p className="flex flex-wrap gap-x-3 text-xs text-fg-muted">
            {frames && <span>{frames}</span>}
            {item.service && (
              <span>
                {providerLabel(item.service)}
                {item.model && ` · ${modelShortName(item.model)}`}
              </span>
            )}
            {item.credits != null && (
              <span className="font-mono tabular">{formatCredits(item.credits)} credits</span>
            )}
          </p>
        )}
        {failed && item.error && <p className="break-words text-xs text-status-failed">{item.error}</p>}
        {item.prompt && (
          <p className="line-clamp-2 text-xs text-fg-secondary" title={item.prompt}>
            {item.prompt}
          </p>
        )}
        {canRegenerate && (
          <div className="mt-auto pt-1">
            <Button size="sm" variant="ghost" className="-ml-2" onClick={() => onRegenerate(kind, item.index ?? 0)}>
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              Regenerate
            </Button>
          </div>
        )}
      </div>
    </li>
  );
}

export function MediaLightbox({ expanded, onClose }) {
  return (
    <Lightbox
      open={!!expanded}
      onOpenChange={(open) => !open && onClose()}
      title={expanded?.label || ""}
      footer={
        expanded?.prompt || expanded?.item?.prompt ? (
          <p className="whitespace-pre-wrap text-sm text-fg-secondary">{expanded.prompt || expanded.item.prompt}</p>
        ) : null
      }
    >
      {expanded &&
        (expanded.kind === "video" ? (
          <video src={expanded.src} controls autoPlay className="max-h-[70vh] max-w-full" />
        ) : (
          <img src={expanded.src} alt={expanded.label} className="max-h-[70vh] max-w-full object-contain" />
        ))}
    </Lightbox>
  );
}

// What a regeneration resets, mirroring the backend's dependency rules.
function regenerateDescription(confirm, pipeline, numVideos) {
  if (!confirm) return null;
  const n = confirm.index + 1;
  const extend = pipeline.video_system === "veo_extend";
  const credits = "This uses new generation credits.";
  if (confirm.kind === "image") {
    if (extend) {
      return `Image ${n} is generated again from the same prompt. Every clip extends from it, so all ${numVideos} clips and the final video are re-rendered. ${credits}`;
    }
    return `Image ${n} is generated again from the same prompt. Every clip that uses it as a frame, and the final video, are re-rendered afterwards. ${credits}`;
  }
  if (extend && n < numVideos) {
    return `Video ${n} is generated again. Each later clip extends the one before it, so clips ${n} to ${numVideos} and the final video are re-rendered. ${credits}`;
  }
  return `Video ${n} is generated again from the same prompt and frames, then the final video is re-rendered. ${credits}`;
}

// Optional model for just the regenerated item; "" keeps the run's model.
function ItemModelPicker({ confirm, pipeline, value, onChange }) {
  const setup = useSetup();
  const isImage = confirm.kind === "image";
  const models = useRemoteList(isImage ? api.getImageModels : api.getVideoModels);
  const extend = pipeline.video_system === "veo_extend";
  if (!isImage && extend && confirm.index > 0) {
    return <p className="mt-3 text-xs text-fg-muted">Later clips inherit clip 1&apos;s model, so the run&apos;s model is used.</p>;
  }
  const current = isImage ? pipeline.image_model : pipeline.video_model;
  const options = [
    { value: "", label: `Same as the run${current ? ` (${modelShortName(current)})` : ""}` },
    ...(models.status === "ok"
      ? isImage
        ? imageModelOptions(models.items, setup)
        : videoModelOptions(models.items, { isExtend: extend, aspect: pipeline.aspect_ratio })
      : []),
  ];
  return (
    <div className="mt-4">
      <SelectField
        id="regenerate-model"
        label={isImage ? "Image model for this image" : "Video model for this clip"}
        value={value}
        onChange={onChange}
        options={options}
        disabled={models.status !== "ok"}
        hint={models.status === "error" ? `Couldn't load models: ${errorMessage(models.error)}` : null}
      />
    </div>
  );
}

/**
 * Images and clips of a run. Regenerating asks for confirmation because it
 * also re-renders everything downstream of the item.
 */
export default function MediaGrid({ pipeline, images, videos, canRegenerate, onRegenerated }) {
  const [expanded, setExpanded] = useState(null);
  const [confirm, setConfirm] = useState(null); // {kind, index}
  const [pending, setPending] = useState(false);
  const [itemModel, setItemModel] = useState("");
  const aspect = pipeline.aspect_ratio || "16:9";

  const regenerate = async () => {
    setPending(true);
    try {
      const models = itemModel ? { [confirm.kind === "image" ? "image_model" : "video_model"]: itemModel } : {};
      const res = await api.regenerate(pipeline.id, confirm.kind, confirm.index, models);
      const imgs = res?.reset_images?.length ?? 0;
      const clips = res?.reset_videos?.length ?? 0;
      toast.success(
        `Regeneration queued: ${imgs} image${imgs === 1 ? "" : "s"} and ${clips} clip${clips === 1 ? "" : "s"}, then the final video.`,
      );
      setConfirm(null);
      onRegenerated?.();
    } catch (err) {
      toast.error(`Couldn't regenerate: ${errorMessage(err)}`);
    } finally {
      setPending(false);
    }
  };

  const section = (kind, items) =>
    items.length > 0 && (
      <section aria-labelledby={`${kind}-heading`}>
        <h2 id={`${kind}-heading`} className="mb-2 flex items-baseline gap-2 text-sm font-medium text-fg">
          {kind === "image" ? "Images" : "Videos"}
          <span className="font-mono text-xs font-normal text-fg-muted tabular">
            {items.filter((i) => i.status === "completed").length}/{items.length}
          </span>
        </h2>
        <ul className={cn("grid gap-3", GRID_CLASS[aspect] || GRID_CLASS["16:9"])}>
          {items.map((item) => (
            <MediaCard
              key={`${kind}-${item.index}`}
              item={item}
              kind={kind}
              aspectRatio={aspect}
              videoSystem={pipeline.video_system}
              canRegenerate={canRegenerate}
              onRegenerate={(k, index) => {
                setItemModel("");
                setConfirm({ kind: k, index });
              }}
              onExpand={setExpanded}
            />
          ))}
        </ul>
      </section>
    );

  const n = confirm ? confirm.index + 1 : 0;
  return (
    <>
      {section("image", images)}
      {section("video", videos)}
      <MediaLightbox expanded={expanded} onClose={() => setExpanded(null)} />
      <ConfirmDialog
        open={!!confirm}
        onOpenChange={(open) => !open && setConfirm(null)}
        pending={pending}
        title={confirm?.kind === "image" ? `Regenerate image ${n}?` : `Regenerate video ${n}?`}
        confirmLabel="Regenerate"
        description={
          <>
            <p>{regenerateDescription(confirm, pipeline, videos.length)}</p>
            {confirm && (
              <ItemModelPicker
                key={`${confirm.kind}-${confirm.index}`}
                confirm={confirm}
                pipeline={pipeline}
                value={itemModel}
                onChange={setItemModel}
              />
            )}
          </>
        }
        onConfirm={regenerate}
      />
    </>
  );
}
