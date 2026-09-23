import { Toaster as Sonner } from "sonner";

export function Toaster() {
  return (
    <Sonner
      theme="system"
      position="bottom-right"
      offset={16}
      toastOptions={{
        unstyled: true,
        classNames: {
          toast:
            "flex w-[calc(100vw-32px)] sm:w-80 items-start gap-2 rounded border border-line bg-surface p-3 text-sm text-fg shadow-menu",
          title: "font-medium",
          description: "text-fg-secondary",
          actionButton: "ml-auto h-7 shrink-0 rounded border border-line bg-raised px-2 text-sm text-fg hover:bg-hover",
          icon: "mt-0.5",
          success: "[&_[data-icon]]:text-status-completed",
          error: "[&_[data-icon]]:text-status-failed",
        },
      }}
    />
  );
}
