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

export const Select = forwardRef(function Select({ className, children, ...props }, ref) {
  return (
    <select ref={ref} className={cn(CONTROL, "h-8 px-2 text-sm", className)} {...props}>
      {children}
    </select>
  );
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

/**
 * Segmented control built on native radio inputs, so arrow keys, form
 * semantics and screen readers work without extra code.
 */
export function Segmented({ name, legend, value, onChange, options, className }) {
  return (
    <fieldset className={className}>
      <legend className="mb-1.5 text-sm font-medium text-fg">{legend}</legend>
      <div className="flex rounded border border-line bg-surface p-0.5">
        {options.map((opt) => {
          const id = `${name}-${opt.value}`;
          const checked = value === opt.value;
          return (
            <label
              key={opt.value}
              htmlFor={id}
              className={cn(
                "relative flex h-7 flex-1 cursor-pointer items-center justify-center gap-1.5 rounded-sm px-2 text-sm",
                "has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-ring",
                checked ? "bg-raised text-fg font-medium" : "text-fg-secondary hover:text-fg",
              )}
            >
              <input
                id={id}
                type="radio"
                name={name}
                value={opt.value}
                checked={checked}
                onChange={() => onChange(opt.value)}
                className="sr-only"
              />
              {opt.label}
              {opt.detail && <span className="font-mono text-2xs text-fg-muted">{opt.detail}</span>}
            </label>
          );
        })}
      </div>
    </fieldset>
  );
}
