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
      // Not the library's `busy`: it puts a spinner on the confirm button. Text only here.
      onClose={() => {
        if (!pending) onCancel();
      }}
      onConfirm={() => {
        if (!pending) onConfirm();
      }}
      title={title}
      tone={tone}
      confirmLabel={pending ? pendingLabel : confirmLabel}
      confirmDisabled={pending}
    >
      <span className="[overflow-wrap:anywhere]">{description}</span>
    </XConfirmDialog>
  );
}
