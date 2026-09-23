import { cn } from "../lib/utils";

/** Right-hand settings column of the editor pages; sticks while the left column scrolls. */
export function SettingsPanel({ children, label }) {
  return (
    <aside
      aria-label={label}
      className="self-start divide-y divide-line rounded border border-line bg-surface lg:sticky lg:top-4"
    >
      {children}
    </aside>
  );
}

/** Titled group of fields with a 12px vertical rhythm. Omit `title` for untitled blocks (estimate, launch). */
export function PanelSection({ title, children, className }) {
  const id = title ? `panel-${title.toLowerCase().replace(/\W+/g, "-")}` : undefined;
  return (
    <section aria-labelledby={id} className={cn("space-y-3 p-4", className)}>
      {title && (
        <h2 id={id} className="text-xs font-medium text-fg-muted">
          {title}
        </h2>
      )}
      {children}
    </section>
  );
}
