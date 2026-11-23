import XConfirmDialog from "xedonium/ConfirmDialog";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  pending?: boolean;
  pendingLabel?: string;
  tone?: "default" | "danger";
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
  tone = "danger",
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  return (
    <XConfirmDialog
      open={open}
      onClose={onCancel}
      onConfirm={onConfirm}
      title={title}
      tone={tone}
      busy={pending}
      confirmLabel={confirmLabel}
      busyLabel={pendingLabel}
    >
      {description}
    </XConfirmDialog>
  );
}
