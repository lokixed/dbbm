import Label from "xedonium/Label";

/** A labelled control in a filter drawer. */
export function FilterCell({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <Label>{label}</Label>
      {children}
    </div>
  );
}
