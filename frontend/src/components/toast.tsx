import { createContext, useContext, useMemo, type ReactNode } from "react";
import Toast from "xedonium/Toast";
import useTimedToast from "xedonium/hooks/useTimedToast";

interface ToastOptions {
  title: string;
  description?: string;
  variant: "success" | "error";
}

interface ToastContextValue {
  toast: (options: ToastOptions) => void;
}

const DISMISS_MS = 6000;
const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: ReactNode }) {
  const { toasts, showToast, hideToast } = useTimedToast(DISMISS_MS);

  const value = useMemo<ToastContextValue>(
    () => ({
      toast: ({ title, description, variant }) =>
        showToast(
          <div className="min-w-0">
            <p className="font-medium">{title}</p>
            {description && <p className="break-words">{description}</p>}
          </div>,
          variant === "success" ? "success" : "danger",
        ),
    }),
    [showToast],
  );

  return (
    <ToastContext.Provider value={value}>
      {children}
      <Toast toasts={toasts} onClose={hideToast} position="bottom-right" />
    </ToastContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside ToastProvider");
  return context;
}
