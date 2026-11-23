import type { FieldValues, Path, PathValue, UseFormReturn } from "react-hook-form";

/**
 * Xedonium controls report plain values (string / boolean) instead of DOM events,
 * so react-hook-form's `register` cannot be spread onto them. These helpers bind a
 * field to a Xedonium control: `<Input {...bindText(form, "name")} />`.
 */
export function bindText<T extends FieldValues>(form: UseFormReturn<T>, name: Path<T>) {
  const raw: unknown = form.watch(name);
  return {
    name,
    value: raw === undefined || raw === null ? "" : String(raw),
    onChange: (value: string) =>
      form.setValue(name, value as PathValue<T, Path<T>>, {
        shouldDirty: true,
        shouldValidate: form.formState.isSubmitted,
      }),
  };
}

export function bindBool<T extends FieldValues>(form: UseFormReturn<T>, name: Path<T>) {
  return {
    name,
    checked: Boolean(form.watch(name)),
    onChange: (checked: boolean) =>
      form.setValue(name, checked as PathValue<T, Path<T>>, {
        shouldDirty: true,
        shouldValidate: form.formState.isSubmitted,
      }),
  };
}
