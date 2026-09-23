import { TriangleAlert } from "lucide-react";

export const SETUP_HELP = "Set them in your .env or hosting provider's environment variables, then restart.";

export function missingReason(feature) {
  if (feature.ready || !feature.missing?.length) return null;
  return `Missing ${feature.missing.join(", ")}. ${SETUP_HELP}`;
}

/** Slim, always-visible notice while a feature's required env vars are missing. */
export default function SetupBanner({ feature, title }) {
  if (feature.ready) return null;
  return (
    <div role="status" className="flex items-start gap-2 border-b border-line bg-surface px-4 py-2 text-sm md:px-6">
      <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0 text-fg" aria-hidden="true" />
      <p className="text-fg-secondary">
        <span className="font-medium text-fg">{title} isn&apos;t configured.</span> Missing{" "}
        {feature.missing.map((name, i) => (
          <span key={name}>
            {i > 0 && ", "}
            <code className="font-mono text-xs text-fg">{name}</code>
          </span>
        ))}
        . {SETUP_HELP}
      </p>
    </div>
  );
}
