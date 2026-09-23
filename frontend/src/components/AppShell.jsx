import { Suspense, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { Bell, FilePlus2, FileText, ListOrdered, LogOut } from "lucide-react";
import { usePipelines } from "../hooks/usePipelines";
import { isActive } from "../lib/format";
import { cn } from "../lib/utils";

const NAV = [
  { to: "/", label: "Queue", icon: ListOrdered, end: true },
  { to: "/new", label: "New pack", icon: FilePlus2 },
  { to: "/script", label: "Script Studio", icon: FileText },
];

function notificationsSupported() {
  return typeof window !== "undefined" && "Notification" in window;
}

// Only offered while permission is undecided; never requested automatically.
function NotificationButton({ className, compact }) {
  const [permission, setPermission] = useState(() =>
    notificationsSupported() ? Notification.permission : "unsupported",
  );
  if (permission !== "default") return null;
  const request = async () => {
    try {
      setPermission(await Notification.requestPermission());
    } catch {
      setPermission("denied");
    }
  };
  return (
    <button
      type="button"
      onClick={request}
      className={className}
      aria-label={compact ? "Enable desktop notifications" : undefined}
    >
      <Bell className="h-4 w-4 shrink-0" aria-hidden="true" />
      {!compact && <span>Enable notifications</span>}
    </button>
  );
}

const railItem = ({ isActive: active }) =>
  cn(
    "flex h-8 items-center gap-2.5 rounded px-2.5 text-sm",
    active ? "bg-raised text-fg font-medium" : "text-fg-secondary hover:bg-hover hover:text-fg",
  );

export default function AppShell({ onSignOut }) {
  const { data } = usePipelines();
  const activeCount = data?.filter((p) => isActive(p.status)).length || 0;

  return (
    <div className="min-h-screen bg-canvas md:flex">
      {/* Desktop rail */}
      <aside className="hidden md:sticky md:top-0 md:flex md:h-screen md:w-56 md:shrink-0 md:flex-col md:border-r md:border-line md:bg-surface">
        <div className="px-4 py-4 text-sm font-semibold text-fg">AI Video Production Hub</div>
        <nav aria-label="Main" className="flex flex-col gap-0.5 px-2">
          {NAV.map(({ to, label, icon: Icon, end }) => (
            <NavLink key={to} to={to} end={end} className={railItem}>
              <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
              <span className="flex-1">{label}</span>
              {to === "/" && activeCount > 0 && (
                <span className="font-mono text-xs text-fg-muted" aria-label={`${activeCount} active`}>
                  {activeCount}
                </span>
              )}
            </NavLink>
          ))}
        </nav>
        <div className="mt-auto flex flex-col gap-0.5 border-t border-line p-2">
          <NotificationButton className="flex h-8 items-center gap-2.5 rounded px-2.5 text-left text-sm text-fg-secondary hover:bg-hover hover:text-fg" />
          <button
            type="button"
            onClick={onSignOut}
            className="flex h-8 items-center gap-2.5 rounded px-2.5 text-sm text-fg-secondary hover:bg-hover hover:text-fg"
          >
            <LogOut className="h-4 w-4 shrink-0" aria-hidden="true" />
            Sign out
          </button>
        </div>
      </aside>

      {/* Mobile top bar */}
      <header className="flex h-12 items-center justify-between border-b border-line bg-surface px-4 md:hidden">
        <span className="text-sm font-semibold text-fg">AI Video Production Hub</span>
        <div className="flex items-center gap-1">
          <NotificationButton
            compact
            className="flex h-8 w-8 items-center justify-center rounded text-fg-secondary hover:bg-hover hover:text-fg"
          />
          <button
            type="button"
            onClick={onSignOut}
            aria-label="Sign out"
            className="flex h-8 w-8 items-center justify-center rounded text-fg-secondary hover:bg-hover hover:text-fg"
          >
            <LogOut className="h-4 w-4" aria-hidden="true" />
          </button>
        </div>
      </header>

      <main className="min-w-0 flex-1 pb-16 md:pb-0">
        {/* Lazy routes load inside the shell so navigation stays visible. */}
        <Suspense fallback={null}>
          <Outlet />
        </Suspense>
      </main>

      {/* Mobile tab bar */}
      <nav
        aria-label="Main"
        className="fixed inset-x-0 bottom-0 z-40 grid h-14 grid-cols-3 border-t border-line bg-surface md:hidden"
      >
        {NAV.map(({ to, label, icon: Icon, end }) => (
          <NavLink
            key={to}
            to={to}
            end={end}
            className={({ isActive: active }) =>
              cn(
                "flex flex-col items-center justify-center gap-0.5 text-2xs",
                active ? "text-fg" : "text-fg-muted hover:text-fg",
              )
            }
          >
            <Icon className="h-4 w-4" aria-hidden="true" />
            {label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
