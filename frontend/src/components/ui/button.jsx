import { forwardRef } from "react";
import { Loader2 } from "lucide-react";
import { cn } from "../../lib/utils";

const VARIANTS = {
  primary: "bg-accent text-accent-fg hover:bg-accent-hover border border-transparent",
  secondary: "bg-raised text-fg border border-line hover:bg-hover hover:border-line-strong",
  ghost: "bg-transparent text-fg-secondary border border-transparent hover:bg-hover hover:text-fg",
  destructive: "bg-destructive text-destructive-fg border border-transparent hover:opacity-90",
};

const SIZES = {
  sm: "h-7 px-2.5 text-sm gap-1.5",
  md: "h-8 px-3 text-sm gap-2",
  lg: "h-9 px-4 text-base gap-2",
  icon: "h-8 w-8 justify-center",
  "icon-sm": "h-7 w-7 justify-center",
};

/**
 * `pending` shows a spinner and disables the button while an action is in
 * flight; the label stays so the layout doesn't jump.
 */
export const Button = forwardRef(function Button(
  { variant = "secondary", size = "md", pending = false, disabled, className, children, type = "button", ...props },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || pending}
      aria-busy={pending || undefined}
      className={cn(
        "inline-flex shrink-0 items-center rounded font-medium whitespace-nowrap transition-colors",
        "disabled:pointer-events-none disabled:opacity-50",
        VARIANTS[variant],
        SIZES[size],
        className,
      )}
      {...props}
    >
      {pending && <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />}
      {children}
    </button>
  );
});

// Same look as Button, for router links and external anchors.
export function buttonClasses({ variant = "secondary", size = "md", className } = {}) {
  return cn(
    "inline-flex shrink-0 items-center rounded font-medium whitespace-nowrap transition-colors",
    VARIANTS[variant],
    SIZES[size],
    className,
  );
}
