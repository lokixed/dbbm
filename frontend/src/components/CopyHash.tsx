import { useState } from "react";
import CheckIcon from "xedonium/icons/Check";
import CopyIcon from "xedonium/icons/Copy";
import { useToast } from "@/components/toast";

/** A shortened hash with its full value in a tooltip and a button that copies it. */
export function CopyHash({ value, length = 12 }: { value: string; length?: number }) {
  const { toast } = useToast();
  const [copied, setCopied] = useState(false);

  const copy = () => {
    navigator.clipboard.writeText(value).then(
      () => {
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1500);
      },
      () => toast({ title: "Could not copy", description: "Clipboard access was denied.", variant: "error" }),
    );
  };

  return (
    <span className="inline-flex items-center gap-1 whitespace-nowrap">
      <code className="text-xs" title={value}>
        {value.slice(0, length)}…
      </code>
      <button
        type="button"
        onClick={copy}
        aria-label="Copy SHA-256"
        title={copied ? "Copied" : "Copy"}
        className="inline-flex h-5 w-5 items-center justify-center text-app-muted transition hover:text-app-text"
      >
        {copied ? <CheckIcon className="h-3.5 w-3.5" /> : <CopyIcon className="h-3.5 w-3.5" />}
      </button>
    </span>
  );
}
