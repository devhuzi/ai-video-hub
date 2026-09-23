import { useState } from "react";
import { ArrowDown, ArrowUp, ChevronDown, ChevronRight, Trash2 } from "lucide-react";
import { Button } from "./ui/button";
import { Textarea } from "./ui/field";
import { countWords } from "../lib/utils";

/**
 * Editable list of parsed prompts. Every change produces a new array via
 * onChange so the parent can re-run mode detection.
 */
export default function PromptList({ title, noun, items, onChange, describe }) {
  const [expanded, setExpanded] = useState(() => new Set());

  const toggle = (i) =>
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(i)) next.delete(i);
      else next.add(i);
      return next;
    });

  const update = (i, text) => onChange(items.map((p, j) => (j === i ? text : p)));
  const move = (i, dir) => {
    const j = i + dir;
    if (j < 0 || j >= items.length) return;
    const next = [...items];
    [next[i], next[j]] = [next[j], next[i]];
    onChange(next);
    setExpanded((prev) => {
      const s = new Set();
      prev.forEach((k) => s.add(k === i ? j : k === j ? i : k));
      return s;
    });
  };
  const remove = (i) => {
    onChange(items.filter((_, j) => j !== i));
    setExpanded((prev) => {
      const s = new Set();
      prev.forEach((k) => {
        if (k < i) s.add(k);
        else if (k > i) s.add(k - 1);
      });
      return s;
    });
  };

  return (
    <section aria-label={title}>
      <h3 className="mb-2 flex items-baseline gap-2 text-sm font-medium text-fg">
        {title}
        <span className="font-mono text-xs text-fg-muted tabular">{items.length}</span>
      </h3>
      {items.length === 0 ? (
        <p className="rounded border border-dashed border-line px-3 py-3 text-sm text-fg-muted">None found.</p>
      ) : (
        <ol className="divide-y divide-line rounded border border-line bg-surface">
          {items.map((text, i) => {
            const label = `${noun} ${i + 1}`;
            const open = expanded.has(i);
            const words = countWords(text);
            const detail = describe?.(i);
            const editorId = `${noun.toLowerCase()}-prompt-${i}`;
            return (
              <li key={i} className="px-3 py-2">
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => toggle(i)}
                    aria-expanded={open}
                    aria-controls={open ? editorId : undefined}
                    className="flex min-w-0 flex-1 items-center gap-1.5 text-left"
                  >
                    {open ? (
                      <ChevronDown className="h-3.5 w-3.5 shrink-0 text-fg-muted" aria-hidden="true" />
                    ) : (
                      <ChevronRight className="h-3.5 w-3.5 shrink-0 text-fg-muted" aria-hidden="true" />
                    )}
                    <span className="text-sm font-medium text-fg">{label}</span>
                    {detail && <span className="truncate text-xs text-fg-muted">{detail}</span>}
                  </button>
                  <span className={`shrink-0 font-mono text-xs tabular ${words ? "text-fg-muted" : "text-status-failed"}`}>
                    {words ? `${words} w` : "empty"}
                  </span>
                  <div className="flex shrink-0">
                    <Button variant="ghost" size="icon-sm" aria-label={`Move ${label} up`} disabled={i === 0} onClick={() => move(i, -1)}>
                      <ArrowUp className="h-3.5 w-3.5" aria-hidden="true" />
                    </Button>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label={`Move ${label} down`}
                      disabled={i === items.length - 1}
                      onClick={() => move(i, 1)}
                    >
                      <ArrowDown className="h-3.5 w-3.5" aria-hidden="true" />
                    </Button>
                    <Button variant="ghost" size="icon-sm" aria-label={`Delete ${label}`} onClick={() => remove(i)}>
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                    </Button>
                  </div>
                </div>
                {open ? (
                  <div className="mt-2">
                    <label htmlFor={editorId} className="sr-only">
                      {label} prompt
                    </label>
                    <Textarea
                      id={editorId}
                      value={text}
                      onChange={(e) => update(i, e.target.value)}
                      rows={Math.min(14, Math.max(4, Math.ceil(text.length / 90)))}
                      className="resize-y"
                    />
                  </div>
                ) : (
                  <p className="ml-5 mt-1 line-clamp-2 text-sm text-fg-secondary">{text || "—"}</p>
                )}
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
