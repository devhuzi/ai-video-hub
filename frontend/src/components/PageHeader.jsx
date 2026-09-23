import { CONTAINER } from "../lib/layout";
import { cn } from "../lib/utils";

export default function PageHeader({ title, meta, actions, children, width = "editor" }) {
  return (
    <div className="border-b border-line">
      <div className={cn(CONTAINER[width], "py-4")}>
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="min-w-0">
            <h1 className="truncate text-lg font-semibold text-fg">{title}</h1>
            {meta && <div className="mt-0.5 text-sm text-fg-secondary">{meta}</div>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </div>
        {children}
      </div>
    </div>
  );
}
