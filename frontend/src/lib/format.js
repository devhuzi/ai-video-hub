// Pipeline status + formatting helpers shared across pages.

export const ACTIVE_STATUSES = new Set(["queued", "pending", "running"]);
export const TERMINAL_STATUSES = new Set(["completed", "failed", "cancelled"]);

export const isActive = (status) => ACTIVE_STATUSES.has(status);

// Status → label + token colour class. Colour is always paired with text.
const STATUS_META = {
  queued: { label: "Queued", tone: "neutral" },
  pending: { label: "Queued", tone: "neutral" },
  running: { label: "Running", tone: "running" },
  paused: { label: "Paused", tone: "paused" },
  completed: { label: "Completed", tone: "completed" },
  failed: { label: "Failed", tone: "failed" },
  cancelled: { label: "Cancelled", tone: "neutral" },
  // Per-item and per-step statuses
  generating: { label: "Generating", tone: "running" },
  generating_backup: { label: "Generating (backup)", tone: "running" },
  active: { label: "In progress", tone: "running" },
  done: { label: "Done", tone: "completed" },
  skipped: { label: "Skipped", tone: "neutral" },
};

export function statusMeta(status) {
  return STATUS_META[status] || { label: humanize(status || "unknown"), tone: "neutral" };
}

export const TONE_TEXT = {
  neutral: "text-status-neutral",
  running: "text-status-running",
  paused: "text-status-paused",
  completed: "text-status-completed",
  failed: "text-status-failed",
};

export const TONE_BG = {
  neutral: "bg-status-neutral",
  running: "bg-status-running",
  paused: "bg-status-paused",
  completed: "bg-status-completed",
  failed: "bg-status-failed",
};

// "generating_images" → "Generating images"
export function humanize(value) {
  if (!value) return "";
  const s = String(value).replace(/[_-]+/g, " ").trim();
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export function pipelineName(p) {
  return p?.name || p?.room_name || "Untitled run";
}

export function pipelineKind(p) {
  if (p?.kind) return p.kind;
  return p?.pipeline_kind === "script" ? "script" : "pack";
}

export const KIND_LABEL = { pack: "Pack", script: "Script" };

const rtf = typeof Intl !== "undefined" && Intl.RelativeTimeFormat
  ? new Intl.RelativeTimeFormat("en", { numeric: "auto", style: "short" })
  : null;

export function relativeTime(iso, now = Date.now()) {
  if (!iso) return "—";
  const t = new Date(iso).getTime();
  if (Number.isNaN(t)) return "—";
  const diff = Math.round((t - now) / 1000);
  const abs = Math.abs(diff);
  if (!rtf) return new Date(iso).toLocaleString();
  if (abs < 45) return "just now";
  if (abs < 3600) return rtf.format(Math.round(diff / 60), "minute");
  if (abs < 86400) return rtf.format(Math.round(diff / 3600), "hour");
  if (abs < 86400 * 30) return rtf.format(Math.round(diff / 86400), "day");
  return new Date(iso).toLocaleDateString();
}

export function absoluteTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? "" : d.toLocaleString();
}

export function formatDuration(seconds) {
  if (seconds == null || Number.isNaN(seconds) || seconds < 0) return "—";
  const s = Math.round(seconds);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  if (m) return `${m}m ${String(sec).padStart(2, "0")}s`;
  return `${sec}s`;
}

// Wall-clock duration of a run: until now while active, until updated_at otherwise.
export function runDuration(p, now = Date.now()) {
  if (!p?.created_at) return null;
  const start = new Date(p.created_at).getTime();
  const end = TERMINAL_STATUSES.has(p.status) || p.status === "paused"
    ? new Date(p.updated_at || p.created_at).getTime()
    : now;
  if (Number.isNaN(start) || Number.isNaN(end)) return null;
  return (end - start) / 1000;
}

export function clampProgress(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.min(100, n));
}
