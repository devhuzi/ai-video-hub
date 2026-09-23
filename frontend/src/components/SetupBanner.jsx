import { Fragment } from "react";
import { TriangleAlert } from "lucide-react";
import { CONTAINER } from "../lib/layout";
import { cn } from "../lib/utils";

// ["A"] → "A"; ["A", "B", "C"] → "A, B and C"
function joinNames(names) {
  return names.length < 2 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
}

function setupHelp(count) {
  return `Add ${count === 1 ? "it" : "them"} to your .env or your host's environment variables and restart.`;
}

// Why the launch button is disabled, or null when the feature is ready.
export function missingReason(feature) {
  if (feature.ready || !feature.missing?.length) return null;
  return `Launching needs ${joinNames(feature.missing)}. ${setupHelp(feature.missing.length)}`;
}

/**
 * Slim, always-visible notice while a feature's required env vars are missing.
 * `lead` reads as the start of a sentence: "prompt packs need", "Script Studio needs".
 */
export default function SetupBanner({ feature, lead, width = "editor" }) {
  if (feature.ready || !feature.missing?.length) return null;
  const names = feature.missing;
  return (
    <div role="status" className="border-b border-line bg-surface">
      <div className={cn(CONTAINER[width], "flex items-start gap-2 py-2 text-sm")}>
        <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-fg" aria-hidden="true" />
        <p className="text-fg-secondary">
          <span className="font-medium text-fg">Setup incomplete</span> — {lead}{" "}
          {names.map((name, i) => (
            <Fragment key={name}>
              {i > 0 && (i === names.length - 1 ? " and " : ", ")}
              <code className="font-mono text-xs text-fg">{name}</code>
            </Fragment>
          ))}
          . {setupHelp(names.length)}
        </p>
      </div>
    </div>
  );
}
