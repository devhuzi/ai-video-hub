import { useMemo } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { MoreHorizontal, Plus } from "lucide-react";
import PageHeader from "../components/PageHeader";
import { ProgressBar, Skeleton, StatusBadge } from "../components/Status";
import { Button, buttonClasses } from "../components/ui/button";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "../components/ui/menu";
import { usePipelines } from "../hooks/usePipelines";
import { availableActions, useRunActions } from "../hooks/useRunActions";
import { errorMessage, mediaSrc } from "../lib/api";
import {
  KIND_LABEL, absoluteTime, clampProgress, formatDuration, humanize, isActive,
  pipelineKind, pipelineName, relativeTime, runDuration,
} from "../lib/format";
import { CONTAINER } from "../lib/layout";
import { cn } from "../lib/utils";

const FILTERS = [
  { key: "all", label: "All", match: () => true },
  { key: "active", label: "Active", match: (p) => isActive(p.status) || p.status === "paused" },
  { key: "failed", label: "Failed", match: (p) => p.status === "failed" },
  { key: "completed", label: "Completed", match: (p) => p.status === "completed" },
];

const COLS =
  "lg:grid lg:grid-cols-[minmax(0,1fr)_112px_minmax(0,160px)_120px_96px_80px_32px] lg:items-center lg:gap-4";

function Thumb({ url }) {
  const src = mediaSrc(url);
  return (
    <div className="aspect-video w-16 shrink-0 overflow-hidden rounded-sm border border-line bg-raised">
      {src && <img src={src} alt="" className="h-full w-full object-cover" loading="lazy" />}
    </div>
  );
}

function RowMenu({ pipeline, actions }) {
  const navigate = useNavigate();
  const allowed = availableActions(pipeline.status);
  const busy = actions.isPending(pipeline.id);
  return (
    <Menu>
      <MenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label={`Actions for ${pipelineName(pipeline)}`} pending={busy}>
          {!busy && <MoreHorizontal className="h-4 w-4" aria-hidden="true" />}
        </Button>
      </MenuTrigger>
      <MenuContent>
        <MenuItem onSelect={() => navigate(`/run/${pipeline.id}`)}>Open</MenuItem>
        {allowed.pause && <MenuItem onSelect={() => actions.request("pause", pipeline)}>Pause</MenuItem>}
        {allowed.resume && <MenuItem onSelect={() => actions.request("resume", pipeline)}>Resume</MenuItem>}
        {allowed.retry && <MenuItem onSelect={() => actions.request("retry", pipeline)}>Retry</MenuItem>}
        {(allowed.cancel || allowed.delete) && <MenuSeparator />}
        {allowed.cancel && <MenuItem onSelect={() => actions.request("cancel", pipeline)}>Cancel…</MenuItem>}
        {allowed.delete && <MenuItem onSelect={() => actions.request("delete", pipeline)}>Delete…</MenuItem>}
      </MenuContent>
    </Menu>
  );
}

function Row({ p, actions, now }) {
  const name = pipelineName(p);
  const kind = pipelineKind(p);
  const pct = clampProgress(p.progress);
  const step = p.status === "completed" ? "—" : humanize(p.current_step) || "—";

  return (
    <li className={cn("relative border-b border-line px-4 py-3 hover:bg-hover md:px-6 lg:py-2", COLS)}>
      <div className="flex min-w-0 items-center gap-3">
        <Thumb url={p.thumbnail_url} />
        <div className="min-w-0 flex-1">
          {/* The link covers the whole row; the menu sits above it. */}
          <Link
            to={`/run/${p.id}`}
            className="block truncate text-base font-medium text-fg after:absolute after:inset-0 after:content-['']"
          >
            {name}
          </Link>
          <div className="mt-0.5 flex items-center gap-2 text-xs text-fg-muted">
            <span>{KIND_LABEL[kind] || kind}</span>
            <span className="lg:hidden" aria-hidden="true">·</span>
            <span className="lg:hidden">
              <StatusBadge status={p.status} className="text-xs" />
            </span>
          </div>
        </div>
        <div className="relative z-10 lg:hidden">
          <RowMenu pipeline={p} actions={actions} />
        </div>
      </div>

      <div className="hidden lg:block">
        <StatusBadge status={p.status} />
      </div>
      <div className="mt-2 truncate text-sm text-fg-secondary lg:mt-0" title={step}>
        <span className="lg:hidden text-fg-muted">Step: </span>
        {step}
      </div>
      <div className="mt-2 flex items-center gap-2 lg:mt-0">
        <ProgressBar value={pct} status={p.status} label={`${name} progress`} />
        <span className="w-9 shrink-0 text-right font-mono text-xs text-fg-secondary tabular">{Math.round(pct)}%</span>
      </div>
      <div className="mt-1 flex gap-3 text-xs text-fg-muted lg:contents">
        <span className="lg:text-sm lg:text-fg-secondary" title={absoluteTime(p.created_at)}>
          {relativeTime(p.created_at, now)}
        </span>
        <span className="font-mono tabular lg:text-sm lg:text-fg-secondary">{formatDuration(runDuration(p, now))}</span>
      </div>
      <div className="relative z-10 hidden lg:block">
        <RowMenu pipeline={p} actions={actions} />
      </div>
    </li>
  );
}

export default function Queue() {
  const { data, error, loading, refresh } = usePipelines();
  const [params, setParams] = useSearchParams();
  const filterKey = FILTERS.some((f) => f.key === params.get("filter")) ? params.get("filter") : "all";
  const actions = useRunActions({ onChanged: refresh, onDeleted: refresh });

  const list = useMemo(
    () => [...(data || [])].sort((a, b) => String(b.created_at).localeCompare(String(a.created_at))),
    [data],
  );
  const counts = useMemo(
    () => Object.fromEntries(FILTERS.map((f) => [f.key, list.filter(f.match).length])),
    [list],
  );
  const filter = FILTERS.find((f) => f.key === filterKey);
  const visible = list.filter(filter.match);
  const now = Date.now();

  const setFilter = (key) => {
    const next = new URLSearchParams(params);
    if (key === "all") next.delete("filter");
    else next.set("filter", key);
    setParams(next, { replace: true });
  };

  let body;
  if (loading && !data) {
    body = (
      <ul aria-label="Loading runs">
        {Array.from({ length: 5 }, (_, i) => (
          <li key={i} className="flex items-center gap-3 border-b border-line px-4 py-3 md:px-6">
            <Skeleton className="aspect-video w-16" />
            <div className="flex-1 space-y-1.5">
              <Skeleton className="h-3.5 w-1/3" />
              <Skeleton className="h-3 w-16" />
            </div>
          </li>
        ))}
      </ul>
    );
  } else if (error && !data) {
    body = (
      <div className="px-4 py-16 text-center md:px-6">
        <p className="text-base font-medium text-fg">Can&apos;t load runs</p>
        <p className="mx-auto mt-1 max-w-md text-sm text-fg-secondary">{errorMessage(error)}</p>
        <Button className="mt-4" onClick={refresh}>
          Try again
        </Button>
      </div>
    );
  } else if (list.length === 0) {
    body = (
      <div className="px-4 py-16 text-center md:px-6">
        <p className="text-base font-medium text-fg">No runs yet</p>
        <p className="mt-1 text-sm text-fg-secondary">Paste a prompt pack or a script to start one.</p>
        <div className="mt-4 flex justify-center gap-2">
          <Link to="/new" className={buttonClasses({ variant: "primary" })}>
            New pack
          </Link>
          <Link to="/script" className={buttonClasses()}>
            Script Studio
          </Link>
        </div>
      </div>
    );
  } else if (visible.length === 0) {
    body = (
      <p className="px-4 py-16 text-center text-sm text-fg-secondary md:px-6">
        No {filter.label.toLowerCase()} runs.
      </p>
    );
  } else {
    body = (
      <>
        <div
          aria-hidden="true"
          className={cn("hidden border-b border-line px-4 py-2 md:px-6 text-xs font-medium text-fg-muted", COLS)}
        >
          <span>Name</span>
          <span>Status</span>
          <span>Step</span>
          <span>Progress</span>
          <span>Created</span>
          <span>Duration</span>
          <span />
        </div>
        <ul aria-label="Runs">
          {visible.map((p) => (
            <Row key={p.id} p={p} actions={actions} now={now} />
          ))}
        </ul>
      </>
    );
  }

  return (
    <>
      <PageHeader
        title="Queue"
        width="wide"
        actions={
          <Link to="/new" className={buttonClasses({ variant: "primary" })}>
            <Plus className="h-4 w-4" aria-hidden="true" />
            New pack
          </Link>
        }
      >
        <div className="mt-3 flex flex-wrap gap-1.5" role="group" aria-label="Filter runs">
          {FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              aria-pressed={filterKey === f.key}
              onClick={() => setFilter(f.key)}
              className={cn(
                "inline-flex h-7 items-center gap-1.5 rounded border px-2.5 text-sm",
                filterKey === f.key
                  ? "border-line-strong bg-raised font-medium text-fg"
                  : "border-line text-fg-secondary hover:bg-hover hover:text-fg",
              )}
            >
              {f.label}
              <span className="font-mono text-xs text-fg-muted tabular">{data ? counts[f.key] : "–"}</span>
            </button>
          ))}
        </div>
      </PageHeader>

      {error && data && (
        <div role="status" className="border-b border-line bg-surface">
          <p className={cn(CONTAINER.wide, "py-2 text-sm text-status-failed")}>
            Last refresh failed: {errorMessage(error)}. Retrying automatically.
          </p>
        </div>
      )}

      {/* Rows carry their own padding so the hover background spans the full row. */}
      <div className="mx-auto w-full max-w-[1440px]">{body}</div>
      {actions.dialog}
    </>
  );
}
