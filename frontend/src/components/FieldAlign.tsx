/**
 * Puts a checkbox or switch on the same row as the input next to it: it leaves room for the input's
 * label and matches the input's height, instead of guessing with padding.
 */
export function FieldAlign({ children, align = "center" }: { children: React.ReactNode; align?: "center" | "start" }) {
  return (
    <div className="flex flex-col gap-1">
      <span aria-hidden="true" className="h-4" />
      <div className={`flex min-h-[2.375rem] ${align === "center" ? "items-center" : "items-start py-2"}`}>{children}</div>
    </div>
  );
}
