import { cn } from "../lib/utils";
import { TONE_BG, TONE_TEXT, clampProgress, statusMeta } from "../lib/format";

// Dot + label: status is never conveyed by colour alone.
export function StatusBadge({ status, className }) {
  const meta = statusMeta(status);
  return (
    <span className={cn("inline-flex items-center gap-1.5 text-sm", TONE_TEXT[meta.tone], className)}>
      <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", TONE_BG[meta.tone])} aria-hidden="true" />
      {meta.label}
    </span>
  );
}

export function ProgressBar({ value, status, label, className }) {
  const pct = clampProgress(value);
  const tone = statusMeta(status).tone;
  return (
    <div
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(pct)}
      aria-label={label}
      className={cn("h-1 w-full overflow-hidden rounded-full bg-raised", className)}
    >
      <div className={cn("h-full rounded-full", TONE_BG[tone])} style={{ width: `${pct}%` }} />
    </div>
  );
}

// Static placeholder block (no shimmer/pulse).
export function Skeleton({ className }) {
  return <div className={cn("rounded bg-raised", className)} aria-hidden="true" />;
}
