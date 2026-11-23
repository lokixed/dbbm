import { cn } from "@/lib/utils";

interface SwitchProps {
  id?: string;
  checked: boolean;
  onChange: (checked: boolean) => void;
  disabled?: boolean;
  label: string;
  /** Hide the visible label (it stays available to screen readers). */
  hideLabel?: boolean;
}

export function Switch({ id, checked, onChange, disabled = false, label, hideLabel = false }: SwitchProps) {
  return (
    <label className="inline-flex cursor-pointer items-center gap-2 text-sm">
      <button
        id={id}
        type="button"
        role="switch"
        aria-checked={checked}
        aria-label={hideLabel ? label : undefined}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={cn(
          "relative inline-flex h-5 w-9 shrink-0 items-center rounded-full border-2 border-transparent transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-50",
          checked ? "bg-primary" : "bg-input",
        )}
      >
        <span
          className={cn(
            "pointer-events-none block h-4 w-4 rounded-full bg-background shadow transition-transform",
            checked ? "translate-x-4" : "translate-x-0",
          )}
        />
      </button>
      {!hideLabel && <span>{label}</span>}
    </label>
  );
}
