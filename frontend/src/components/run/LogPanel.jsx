import { useEffect, useRef, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";
import { cn } from "../../lib/utils";

/**
 * Collapsible log viewer. "Follow" keeps the log container scrolled to the
 * newest line — it only ever scrolls the container, never the page.
 */
export default function LogPanel({ logs = [] }) {
  const [open, setOpen] = useState(false);
  const [follow, setFollow] = useState(true);
  const box = useRef(null);

  useEffect(() => {
    if (open && follow && box.current) box.current.scrollTop = box.current.scrollHeight;
  }, [open, follow, logs.length]);

  const onScroll = () => {
    const el = box.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 8;
    if (!atBottom && follow) setFollow(false);
  };

  return (
    <section aria-labelledby="logs-heading" className="rounded border border-line bg-surface">
      <div className="flex items-center justify-between gap-2 px-4 py-2.5">
        <h2 id="logs-heading" className="text-sm font-medium text-fg">
          <button
            type="button"
            aria-expanded={open}
            aria-controls="log-lines"
            onClick={() => setOpen((o) => !o)}
            className="flex items-center gap-1.5"
          >
            {open ? (
              <ChevronDown className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />
            ) : (
              <ChevronRight className="h-3.5 w-3.5 text-fg-muted" aria-hidden="true" />
            )}
            Logs
            <span className="font-mono text-xs font-normal text-fg-muted tabular">{logs.length}</span>
          </button>
        </h2>
        {open && (
          <button
            type="button"
            aria-pressed={follow}
            onClick={() => setFollow((f) => !f)}
            className={cn(
              "h-7 rounded border px-2.5 text-sm",
              follow ? "border-line-strong bg-raised text-fg" : "border-line text-fg-secondary hover:bg-hover",
            )}
          >
            Follow
          </button>
        )}
      </div>
      {open && (
        <div
          id="log-lines"
          ref={box}
          onScroll={onScroll}
          // Scrollable region must be focusable so keyboard users can scroll it.
          // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
          tabIndex={0}
          role="region"
          aria-label="Log lines"
          className="max-h-96 overflow-auto border-t border-line bg-canvas px-4 py-2 font-mono text-xs leading-5 text-fg-secondary"
        >
          {logs.length === 0 ? (
            <p className="text-fg-muted">No log lines yet.</p>
          ) : (
            logs.map((line, i) => (
              <div key={i} className="whitespace-pre-wrap break-words">
                {line}
              </div>
            ))
          )}
        </div>
      )}
    </section>
  );
}
