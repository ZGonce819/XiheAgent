import { Dialog } from "@base-ui/react/dialog"
import { AlertTriangle, LoaderCircle } from "lucide-react"

import { Button } from "@/components/ui/button"

interface ConfirmDialogProps {
  open: boolean
  title: string
  description: string
  confirmText?: string
  cancelText?: string
  destructive?: boolean
  busy?: boolean
  showCancel?: boolean
  onOpenChange: (open: boolean) => void
  onConfirm: () => void
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmText = "确认",
  cancelText = "取消",
  destructive = false,
  busy = false,
  showCancel = true,
  onOpenChange,
  onConfirm,
}: ConfirmDialogProps) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-50 bg-black/40 data-open:animate-in data-open:fade-in-0 data-closed:animate-out data-closed:fade-out-0" />
        <Dialog.Popup className="fixed top-1/2 left-1/2 z-50 w-[min(27rem,calc(100vw-2rem))] -translate-x-1/2 -translate-y-1/2 rounded-lg border border-border bg-popover p-5 text-popover-foreground shadow-xl outline-none data-open:animate-in data-open:fade-in-0 data-open:zoom-in-95 data-closed:animate-out data-closed:fade-out-0 data-closed:zoom-out-95">
          <div className="flex items-start gap-3">
            <span className="grid size-8 shrink-0 place-items-center rounded-md bg-destructive/10 text-destructive">
              <AlertTriangle className="size-4" />
            </span>
            <div className="min-w-0 flex-1">
              <Dialog.Title className="text-sm font-semibold">{title}</Dialog.Title>
              <Dialog.Description className="mt-2 whitespace-pre-line text-sm leading-6 text-muted-foreground">
                {description}
              </Dialog.Description>
            </div>
          </div>
          <div className="mt-5 flex justify-end gap-2">
            {showCancel && (
              <Button variant="outline" size="sm" disabled={busy} onClick={() => onOpenChange(false)}>
                {cancelText}
              </Button>
            )}
            <Button
              variant={destructive ? "destructive" : "default"}
              size="sm"
              disabled={busy}
              onClick={onConfirm}
            >
              {busy && <LoaderCircle className="size-3.5 animate-spin" />}
              {confirmText}
            </Button>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
