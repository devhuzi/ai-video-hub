import { useCallback } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import PageHeader from "../components/PageHeader";
import { ProgressBar, Skeleton, StatusBadge } from "../components/Status";
import { Button, buttonClasses } from "../components/ui/button";
import FinalVideo from "../components/run/FinalVideo";
import StepTimeline from "../components/run/StepTimeline";
import MediaGrid from "../components/run/MediaGrid";
import SceneList from "../components/run/SceneList";
import LogPanel from "../components/run/LogPanel";
import { mediaItems } from "../components/run/media";
import { usePipelines } from "../hooks/usePipelines";
import { usePolling } from "../hooks/usePolling";
import { availableActions, useRunActions } from "../hooks/useRunActions";
import { api, errorMessage } from "../lib/api";
import {
  KIND_LABEL, absoluteTime, clampProgress, formatDuration, humanize, isActive, pipelineKind, pipelineName,
  relativeTime, runDuration,
} from "../lib/format";

const ACTIVE_INTERVAL = 2500;
const ERROR_INTERVAL = 5000;

const VIDEO_SYSTEM_LABEL = {
  veo31_frame: "Veo 3.1 first → last frame",
  grok_sequential_extend: "Sequential extend",
};

function BackLink() {
  return (
    <Link to="/" className="inline-flex items-center gap-1.5 text-sm text-fg-secondary hover:text-fg">
      <ArrowLeft className="h-3.5 w-3.5" aria-hidden="true" />
      Queue
    </Link>
  );
}

function Detail({ label, children }) {
  return (
    <div className="flex justify-between gap-3 py-1.5 text-sm">
      <dt className="text-fg-muted">{label}</dt>
      <dd className="min-w-0 truncate text-right text-fg">{children}</dd>
    </div>
  );
}

export default function RunDetail() {
  const { id } = useParams();
  const navigate = useNavigate();
  const { refresh: refreshQueue } = usePipelines();

  const fetcher = useCallback((signal) => api.getPipeline(id, signal), [id]);
  const { data: p, error, loading, refresh } = usePolling(
    fetcher,
    (data, err) => {
      if (err) return err.status === 404 ? null : ERROR_INTERVAL;
      return data && isActive(data.status) ? ACTIVE_INTERVAL : null;
    },
    id,
  );

  const actions = useRunActions({
    onChanged: () => {
      refresh();
      refreshQueue();
    },
    onDeleted: () => {
      refreshQueue();
      navigate("/");
    },
  });

  if (error?.status === 404) {
    return (
      <div className="px-4 py-16 text-center md:px-6">
        <p className="text-base font-medium text-fg">Run not found</p>
        <p className="mt-1 text-sm text-fg-secondary">It may have been deleted, or the link is wrong.</p>
        <Link to="/" className={buttonClasses({ className: "mt-4" })}>
          Back to queue
        </Link>
      </div>
    );
  }

  if (!p) {
    if (error && !loading) {
      return (
        <div className="px-4 py-16 text-center md:px-6">
          <p className="text-base font-medium text-fg">Can&apos;t load this run</p>
          <p className="mx-auto mt-1 max-w-md text-sm text-fg-secondary">{errorMessage(error)}</p>
          <Button className="mt-4" onClick={refresh}>
            Try again
          </Button>
        </div>
      );
    }
    return (
      <div className="space-y-4 px-4 py-5 md:px-6" aria-busy="true" aria-label="Loading run">
        <Skeleton className="h-5 w-64" />
        <Skeleton className="h-4 w-40" />
        <Skeleton className="aspect-video w-full max-w-2xl" />
      </div>
    );
  }

  const name = pipelineName(p);
  const kind = pipelineKind(p);
  const allowed = availableActions(p.status);
  const busy = (action) => actions.isPending(p.id, action);
  const anyBusy = actions.isPending(p.id);
  const images = mediaItems(p, "image");
  const videos = mediaItems(p, "video");
  const scenes = p.scenes || p.scene_details || [];
  const canRegenerate = kind === "pack" && !isActive(p.status);
  const active = isActive(p.status);

  return (
    <>
      <div className="px-4 pt-3 md:px-6">
        <BackLink />
      </div>
      <PageHeader
        title={name}
        meta={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            <StatusBadge status={p.status} />
            <span>{KIND_LABEL[kind] || kind}</span>
            <span title={absoluteTime(p.created_at)}>Created {relativeTime(p.created_at)}</span>
            <span className="font-mono tabular">{formatDuration(runDuration(p))}</span>
          </span>
        }
        actions={
          <>
            {allowed.pause && (
              <Button pending={busy("pause")} disabled={anyBusy} onClick={() => actions.request("pause", p)}>
                Pause
              </Button>
            )}
            {allowed.resume && (
              <Button variant="primary" pending={busy("resume")} disabled={anyBusy} onClick={() => actions.request("resume", p)}>
                Resume
              </Button>
            )}
            {allowed.retry && (
              <Button variant="primary" pending={busy("retry")} disabled={anyBusy} onClick={() => actions.request("retry", p)}>
                Retry
              </Button>
            )}
            {allowed.cancel && (
              <Button disabled={anyBusy} onClick={() => actions.request("cancel", p)}>
                Cancel
              </Button>
            )}
            {allowed.delete && (
              <Button variant="ghost" disabled={anyBusy} onClick={() => actions.request("delete", p)}>
                Delete
              </Button>
            )}
          </>
        }
      >
        {active && (
          <div className="mt-3 flex items-center gap-3">
            <ProgressBar value={p.progress} status={p.status} label="Run progress" className="max-w-md" />
            <span className="font-mono text-xs text-fg-secondary tabular">{Math.round(clampProgress(p.progress))}%</span>
            <span className="truncate text-sm text-fg-secondary">{humanize(p.current_step)}</span>
          </div>
        )}
      </PageHeader>

      {error && (
        <div role="status" className="border-b border-line bg-surface px-4 py-2 text-sm text-status-failed md:px-6">
          Last refresh failed: {errorMessage(error)}. Retrying automatically.
        </div>
      )}

      <div className="grid gap-6 px-4 py-5 md:px-6 lg:grid-cols-[minmax(0,1fr)_280px]">
        <div className="min-w-0 space-y-6">
          {p.final_video_url && <FinalVideo url={p.final_video_url} name={name} aspectRatio={p.aspect_ratio} />}

          {p.error_message && (
            <section aria-labelledby="error-heading" className="rounded border border-status-failed/50 bg-surface px-4 py-3">
              <h2 id="error-heading" className="text-sm font-medium text-status-failed">
                Error
              </h2>
              <p className="mt-1 whitespace-pre-wrap break-words font-mono text-xs text-fg-secondary">{p.error_message}</p>
            </section>
          )}

          {kind === "script" && <SceneList scenes={scenes} aspectRatio={p.aspect_ratio} />}

          <MediaGrid
            pipeline={p}
            images={images}
            videos={videos}
            canRegenerate={canRegenerate}
            onRegenerated={() => {
              refresh();
              refreshQueue();
            }}
          />

          {kind === "pack" && images.length === 0 && videos.length === 0 && (
            <p className="text-sm text-fg-muted">No images or clips yet.</p>
          )}

          <LogPanel logs={p.logs || []} />
        </div>

        <aside className="space-y-6 self-start lg:sticky lg:top-4">
          <StepTimeline steps={p.steps} pipeline={p} />
          <section aria-labelledby="details-heading">
            <h2 id="details-heading" className="mb-1 text-sm font-medium text-fg">
              Details
            </h2>
            <dl className="divide-y divide-line">
              <Detail label="Aspect ratio">{p.aspect_ratio || "—"}</Detail>
              {kind === "pack" && (
                <Detail label="Pipeline">{VIDEO_SYSTEM_LABEL[p.video_system] || humanize(p.video_system) || "—"}</Detail>
              )}
              {kind === "pack" && (
                <Detail label="Images / videos">
                  <span className="font-mono tabular">
                    {p.num_images ?? images.length} / {p.num_videos ?? videos.length}
                  </span>
                </Detail>
              )}
              <Detail label="Updated">
                <span title={absoluteTime(p.updated_at)}>{relativeTime(p.updated_at)}</span>
              </Detail>
              <Detail label="ID">
                <span className="font-mono text-xs" title={p.id}>
                  {String(p.id).slice(0, 8)}
                </span>
              </Detail>
            </dl>
          </section>
        </aside>
      </div>
      {actions.dialog}
    </>
  );
}
