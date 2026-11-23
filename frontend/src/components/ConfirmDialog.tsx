import XConfirmDialog from "xedonium/ConfirmDialog";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  pending?: boolean;
  pendingLabel?: string;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  pending = false,
  pendingLabel = "Deleting...",
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <XConfirmDialog
      open={open}
      onClose={onCancel}
      onConfirm={onConfirm}
      title={title}
      tone="danger"
      busy={pending}
      confirmLabel={confirmLabel}
      busyLabel={pendingLabel}
    >
      {description}
    </XConfirmDialog>
  );
}
