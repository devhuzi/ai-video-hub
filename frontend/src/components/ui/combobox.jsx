import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown } from "lucide-react";
import { cn } from "../../lib/utils";

// Every whitespace-separated term must appear in the value, label or group.
function matches(opt, terms) {
  if (!terms.length) return true;
  const hay = `${opt.value} ${opt.label} ${opt.group || ""}`.toLowerCase();
  return terms.every((t) => hay.includes(t));
}

/**
 * Searchable single-select for long lists (e.g. hundreds of OpenRouter models).
 * ARIA combobox pattern: typing filters, ↑/↓ move, Enter picks, Esc closes
 * and restores the selection. Options that share a `group` are listed under a
 * group heading, in the order the groups first appear.
 *
 * options: [{ value, label, detail?, meta?, group? }]
 *   `detail` is a second muted line (e.g. the model id); `meta` sits on the
 *   right (e.g. the price).
 * allowCustom: when true, a typed value that isn't in the list can be used
 *   (for when the list failed to load).
 * onChange receives the chosen value (string).
 */
export function Combobox({
  id,
  value,
  onChange,
  options,
  placeholder = "Search…",
  emptyText = "No matches.",
  disabled,
  invalid,
  allowCustom = false,
  className,
  "aria-describedby": ariaDescribedBy,
}) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(-1);
  const listRef = useRef(null);
  const listId = `${id}-listbox`;
  const selected = options.find((o) => o.value === value);

  const { items, flat } = useMemo(() => {
    const terms = query.toLowerCase().split(/\s+/).filter(Boolean);
    const filtered = options.filter((o) => matches(o, terms));
    const order = [];
    const byGroup = new Map();
    for (const o of filtered) {
      const g = o.group || "";
      if (!byGroup.has(g)) {
        byGroup.set(g, []);
        order.push(g);
      }
      byGroup.get(g).push(o);
    }
    const flatList = order.flatMap((g) => byGroup.get(g));
    const trimmed = query.trim();
    if (allowCustom && trimmed && !options.some((o) => o.value === trimmed)) {
      flatList.unshift({ value: trimmed, label: `Use "${trimmed}"`, custom: true });
    }
    return { items: order.map((g) => ({ group: g, options: byGroup.get(g) })), flat: flatList };
  }, [options, query, allowCustom]);

  const optionId = (i) => `${id}-option-${i}`;

  // Keep the highlighted option visible while navigating.
  useEffect(() => {
    if (!open || active < 0) return;
    const el = listRef.current?.querySelector(`#${CSS.escape(optionId(active))}`);
    el?.scrollIntoView({ block: "nearest" });
    // optionId only depends on id
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, active]);

  // The query is always empty while closed, so `flat` is the full list here.
  const openList = () => {
    if (disabled || open) return;
    setOpen(true);
    setActive(Math.max(0, flat.findIndex((o) => o.value === value)));
  };

  const close = () => {
    setOpen(false);
    setQuery("");
    setActive(-1);
  };

  const pick = (opt) => {
    if (!opt) return;
    onChange(opt.value);
    close();
  };

  const onKeyDown = (e) => {
    if (e.key === "ArrowDown" || e.key === "ArrowUp") {
      e.preventDefault();
      if (!open) {
        openList();
        return;
      }
      if (!flat.length) return;
      const step = e.key === "ArrowDown" ? 1 : -1;
      setActive((a) => (a < 0 ? 0 : (a + step + flat.length) % flat.length));
    } else if (e.key === "Enter") {
      if (!open) return;
      e.preventDefault();
      pick(flat[active]);
    } else if (e.key === "Escape") {
      if (open) {
        e.preventDefault();
        close();
      }
    } else if (e.key === "Home" && open) {
      e.preventDefault();
      setActive(0);
    } else if (e.key === "End" && open) {
      e.preventDefault();
      setActive(flat.length - 1);
    }
  };

  let index = -1;
  const renderOption = (opt) => {
    index += 1;
    const i = index;
    const isActive = i === active;
    const isSelected = opt.value === value && !opt.custom;
    return (
      // Keyboard selection is handled on the input (aria-activedescendant pattern).
      // eslint-disable-next-line jsx-a11y/click-events-have-key-events
      <li
        key={opt.custom ? "__custom" : opt.value}
        id={optionId(i)}
        role="option"
        aria-selected={isSelected}
        onMouseMove={() => active !== i && setActive(i)}
        onClick={() => pick(opt)}
        className={cn(
          "flex cursor-pointer items-start gap-3 rounded-sm px-2 py-1.5",
          isActive && "bg-hover",
        )}
      >
        <span className="min-w-0 flex-1">
          <span className={cn("block truncate text-sm", isSelected ? "font-medium text-fg" : "text-fg")}>
            {opt.label}
          </span>
          {opt.detail && <span className="block truncate font-mono text-2xs text-fg-muted">{opt.detail}</span>}
        </span>
        {opt.meta && <span className="shrink-0 pt-0.5 font-mono text-2xs text-fg-secondary tabular">{opt.meta}</span>}
      </li>
    );
  };

  const custom = flat[0]?.custom ? flat[0] : null;

  return (
    <div className={cn("relative", className)}>
      <input
        id={id}
        type="text"
        role="combobox"
        aria-expanded={open}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={open && active >= 0 && flat.length ? optionId(active) : undefined}
        aria-invalid={invalid || undefined}
        aria-describedby={ariaDescribedBy}
        autoComplete="off"
        spellCheck={false}
        disabled={disabled}
        value={open ? query : selected?.label || value || ""}
        placeholder={open ? selected?.label || placeholder : placeholder}
        onChange={(e) => {
          if (!open) setOpen(true);
          setQuery(e.target.value);
          setActive(0);
        }}
        onClick={openList}
        onKeyDown={onKeyDown}
        onBlur={close}
        className={cn(
          "h-8 w-full truncate rounded border border-line bg-surface pl-2.5 pr-8 text-sm text-fg placeholder:text-fg-muted",
          "hover:border-line-strong focus-visible:border-ring",
          "disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:border-line",
          invalid && "border-status-failed",
        )}
      />
      <ChevronDown
        className={cn(
          "pointer-events-none absolute right-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-muted",
          disabled && "opacity-50",
        )}
        aria-hidden="true"
      />
      {open && (
        <ul
          ref={listRef}
          id={listId}
          role="listbox"
          aria-label="Options"
          // Keep focus in the input so clicks (and scrollbar drags) don't blur it.
          onMouseDown={(e) => e.preventDefault()}
          className="absolute left-0 right-0 top-full z-50 mt-1 max-h-72 overflow-y-auto rounded border border-line bg-surface p-1 shadow-menu"
        >
          {custom && renderOption(custom)}
          {items.map((g) =>
            g.group ? (
              <li key={`group-${g.group}`} role="presentation">
                <div className="px-2 pb-1 pt-2 text-2xs font-medium text-fg-muted">{g.group}</div>
                <ul role="group" aria-label={g.group}>
                  {g.options.map(renderOption)}
                </ul>
              </li>
            ) : (
              g.options.map(renderOption)
            ),
          )}
          {flat.length === 0 && <li className="px-2 py-1.5 text-sm text-fg-muted">{emptyText}</li>}
        </ul>
      )}
    </div>
  );
}
