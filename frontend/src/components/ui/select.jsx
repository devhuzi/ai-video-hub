import { forwardRef } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "../../lib/utils";
import { FieldError, Hint, Label } from "./field";

// Joins aria-describedby ids, skipping empty ones.
export function describedBy(...ids) {
  const joined = ids.filter(Boolean).join(" ");
  return joined || undefined;
}

// "fal.ai" + "add FAL_KEY" → "fal.ai — add FAL_KEY". Native <option>s only render text.
export function optionText(opt) {
  return opt.suffix ? `${opt.label} — ${opt.suffix}` : opt.label;
}

/**
 * Native <select> styled with the design tokens. Native keeps keyboard,
 * mobile pickers and form semantics for free.
 *
 * options: [{ value, label, disabled?, suffix?, group? }]
 *   `suffix` is appended to the label (e.g. why the option is disabled).
 *   Options that share a `group` are rendered inside an <optgroup>.
 * onChange receives the selected value (string), not the event.
 */
export const Select = forwardRef(function Select(
  { id, value, onChange, options, placeholder, disabled, invalid, className, ...props },
  ref,
) {
  const groups = [];
  for (const opt of options) {
    const last = groups[groups.length - 1];
    if (last && last.name === (opt.group || null)) last.items.push(opt);
    else groups.push({ name: opt.group || null, items: [opt] });
  }
  const renderOption = (opt) => (
    <option key={opt.value} value={opt.value} disabled={opt.disabled}>
      {optionText(opt)}
    </option>
  );

  return (
    <div className={cn("relative", className)}>
      <select
        ref={ref}
        id={id}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        disabled={disabled}
        aria-invalid={invalid || undefined}
        className={cn(
          "h-8 w-full appearance-none truncate rounded border border-line bg-surface pl-2.5 pr-8 text-sm text-fg",
          "hover:border-line-strong focus-visible:border-ring",
          "disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-line",
          invalid && "border-status-failed",
        )}
        {...props}
      >
        {placeholder != null && (
          <option value="" disabled>
            {placeholder}
          </option>
        )}
        {groups.map((g, i) =>
          g.name ? (
            <optgroup key={`${g.name}-${i}`} label={g.name}>
              {g.items.map(renderOption)}
            </optgroup>
          ) : (
            g.items.map(renderOption)
          ),
        )}
      </select>
      <ChevronDown
        className={cn(
          "pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-muted",
          disabled && "opacity-50",
        )}
        aria-hidden="true"
      />
    </div>
  );
});

/**
 * Label above, control, one-line hint and error below — with the ids wired
 * so the hint and error are announced with the control.
 * `control` is a render prop receiving { id, "aria-describedby" } for
 * non-Select controls (e.g. the Combobox); otherwise a Select is rendered.
 */
export function SelectField({ id, label, labelHint, hint, error, control, ...selectProps }) {
  const hintId = hint ? `${id}-hint` : null;
  const errorId = error ? `${id}-error` : null;
  const aria = describedBy(errorId, hintId, selectProps["aria-describedby"]);
  return (
    <div>
      <Label htmlFor={id} hint={labelHint}>
        {label}
      </Label>
      {control ? (
        control({ id, "aria-describedby": aria, invalid: !!error })
      ) : (
        <Select id={id} {...selectProps} invalid={!!error} aria-describedby={aria} />
      )}
      <FieldError id={errorId}>{error}</FieldError>
      {hint && <Hint id={hintId}>{hint}</Hint>}
    </div>
  );
}
