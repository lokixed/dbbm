import HelperText from "xedonium/HelperText";
import Label from "xedonium/Label";

interface FormFieldProps {
  id: string;
  label: string;
  error?: string;
  hint?: string;
  children: React.ReactNode;
}

export function FormField({ id, label, error, hint, children }: FormFieldProps) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      {children}
      {hint && !error && <HelperText>{hint}</HelperText>}
      {error && (
        <HelperText role="alert" className="!text-red-600 dark:!text-red-400">
          {error}
        </HelperText>
      )}
    </div>
  );
}
