import { CheckCircle2, XCircle } from "lucide-react";
import { createContext, useCallback, useContext, useMemo, useRef, useState } from "react";
import { cn } from "@/lib/utils";

interface ToastItem {
  id: number;
  title: string;
  description?: string;
  variant: "success" | "error";
}

type ToastOptions = Omit<ToastItem, "id">;

interface ToastContextValue {
  toast: (options: ToastOptions) => void;
}

const DISMISS_MS = 6000;
const ToastContext = createContext<ToastContextValue | null>(null);

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);
  const nextId = useRef(1);

  const toast = useCallback((options: ToastOptions) => {
    const id = nextId.current++;
    setItems((current) => [...current, { id, ...options }]);
    window.setTimeout(() => setItems((current) => current.filter((t) => t.id !== id)), DISMISS_MS);
  }, []);

  const value = useMemo(() => ({ toast }), [toast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        role="status"
        aria-live="polite"
        className="pointer-events-none fixed bottom-4 right-4 z-[100] flex w-80 flex-col gap-2"
      >
        {items.map((item) => (
          <div
            key={item.id}
            className={cn(
              "pointer-events-auto flex gap-3 rounded-md border bg-card p-3 text-sm shadow-lg",
              item.variant === "error" && "border-destructive/50",
            )}
          >
            {item.variant === "success" ? (
              <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-green-600" />
            ) : (
              <XCircle className="mt-0.5 h-4 w-4 shrink-0 text-destructive" />
            )}
            <div className="min-w-0">
              <p className="font-medium">{item.title}</p>
              {item.description && (
                <p className="break-words text-muted-foreground">{item.description}</p>
              )}
            </div>
          </div>
        ))}
      </div>
    </ToastContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside ToastProvider");
  return context;
}
