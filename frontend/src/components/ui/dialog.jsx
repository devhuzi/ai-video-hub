import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { Button } from "./button";
import { cn } from "../../lib/utils";

const OVERLAY = "fixed inset-0 z-50 bg-overlay/60";
const PANEL =
  "fixed left-1/2 top-1/2 z-50 -translate-x-1/2 -translate-y-1/2 rounded border border-line bg-surface shadow-menu";

/**
 * Confirmation for destructive / irreversible actions. Stays open while the
 * action runs (the confirm button shows a pending state) and closes on success.
 */
export function ConfirmDialog({
  open,
  onOpenChange,
  title,
  description,
  confirmLabel,
  destructive = false,
  pending = false,
  onConfirm,
}) {
  return (
    <AlertDialog.Root open={open} onOpenChange={(next) => !pending && onOpenChange(next)}>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className={OVERLAY} />
        <AlertDialog.Content className={cn(PANEL, "w-[calc(100vw-32px)] max-w-md p-5")}>
          <AlertDialog.Title className="text-md font-semibold text-fg">{title}</AlertDialog.Title>
          <AlertDialog.Description asChild>
            <div className="mt-2 text-sm text-fg-secondary">{description}</div>
          </AlertDialog.Description>
          <div className="mt-5 flex justify-end gap-2">
            <AlertDialog.Cancel asChild>
              <Button variant="secondary" disabled={pending}>
                Keep it
              </Button>
            </AlertDialog.Cancel>
            <Button
              variant={destructive ? "destructive" : "primary"}
              pending={pending}
              onClick={(e) => {
                e.preventDefault();
                onConfirm();
              }}
            >
              {confirmLabel}
            </Button>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}

export function Lightbox({ open, onOpenChange, title, children, footer }) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className={cn(OVERLAY, "bg-overlay/80")} />
        <Dialog.Content
          className={cn(PANEL, "flex max-h-[calc(100vh-32px)] w-[calc(100vw-32px)] max-w-5xl flex-col")}
          aria-describedby={undefined}
        >
          <div className="flex items-center justify-between border-b border-line px-4 py-2.5">
            <Dialog.Title className="text-sm font-medium text-fg">{title}</Dialog.Title>
            <Dialog.Close asChild>
              <Button variant="ghost" size="icon-sm" aria-label="Close">
                <X className="h-4 w-4" aria-hidden="true" />
              </Button>
            </Dialog.Close>
          </div>
          <div className="flex min-h-0 flex-1 items-center justify-center bg-canvas p-2">{children}</div>
          {footer && <div className="max-h-40 overflow-y-auto border-t border-line px-4 py-3">{footer}</div>}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
