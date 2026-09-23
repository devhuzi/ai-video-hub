import { Check, Circle, Loader2, Minus, Pause, X } from "lucide-react";
import { ProgressBar } from "../Status";
import { clampProgress, humanize } from "../../lib/format";
import { cn } from "../../lib/utils";

const STEP_STATUS = {
  pending: { icon: Circle, className: "text-fg-muted", text: "Pending" },
  active: { icon: Loader2, className: "text-status-running animate-spin", text: "In progress" },
  done: { icon: Check, className: "text-status-completed", text: "Done" },
  failed: { icon: X, className: "text-status-failed", text: "Failed" },
  paused: { icon: Pause, className: "text-status-paused", text: "Paused" },
  skipped: { icon: Minus, className: "text-status-neutral", text: "Skipped" },
};

export default function StepTimeline({ steps, pipeline }) {
  return (
    <section aria-labelledby="steps-heading">
      <h2 id="steps-heading" className="mb-2 text-sm font-medium text-fg">
        Steps
      </h2>
      {Array.isArray(steps) && steps.length > 0 ? (
        <ol className="space-y-0">
          {steps.map((step, i) => {
            const meta = STEP_STATUS[step.status] || STEP_STATUS.pending;
            const Icon = meta.icon;
            const last = i === steps.length - 1;
            return (
              <li key={step.key || i} className="relative flex gap-3 pb-3">
                {!last && <span className="absolute left-[7px] top-5 h-[calc(100%-16px)] w-px bg-line" aria-hidden="true" />}
                <Icon className={cn("mt-0.5 h-4 w-4 shrink-0", meta.className)} aria-hidden="true" />
                <div className="min-w-0">
                  <p className={cn("text-sm", step.status === "pending" || step.status === "skipped" ? "text-fg-secondary" : "text-fg")}>
                    {step.label || humanize(step.key)}
                  </p>
                  <p className="text-xs text-fg-muted">{meta.text}</p>
                </div>
              </li>
            );
          })}
        </ol>
      ) : (
        <div>
          <p className="text-sm text-fg">{humanize(pipeline.current_step) || "—"}</p>
          <ProgressBar className="mt-2" value={clampProgress(pipeline.progress)} status={pipeline.status} label="Run progress" />
        </div>
      )}
    </section>
  );
}
