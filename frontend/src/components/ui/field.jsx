import { forwardRef } from "react";
import { cn } from "../../lib/utils";

const CONTROL =
  "w-full rounded border border-line bg-surface text-fg placeholder:text-fg-muted " +
  "hover:border-line-strong focus-visible:border-ring disabled:opacity-50";

export function Label({ htmlFor, children, hint, className }) {
  return (
    <div className={cn("mb-1.5 flex items-baseline justify-between gap-2", className)}>
      <label htmlFor={htmlFor} className="text-sm font-medium text-fg">
        {children}
      </label>
      {hint && <span className="text-xs text-fg-muted">{hint}</span>}
    </div>
  );
}

export const Input = forwardRef(function Input({ className, ...props }, ref) {
  return <input ref={ref} className={cn(CONTROL, "h-8 px-2.5 text-sm", className)} {...props} />;
});

export const Textarea = forwardRef(function Textarea({ className, ...props }, ref) {
  return <textarea ref={ref} className={cn(CONTROL, "px-3 py-2 text-sm leading-5", className)} {...props} />;
});

export function Hint({ id, children, className }) {
  return (
    <p id={id} className={cn("mt-1.5 text-xs text-fg-muted", className)}>
      {children}
    </p>
  );
}

export function FieldError({ id, children, className }) {
  if (!children) return null;
  return (
    <p id={id} role="alert" className={cn("mt-1.5 text-xs text-status-failed", className)}>
      {children}
    </p>
  );
}
