import { useState } from "react";
import CheckIcon from "xedonium/icons/Check";
import CopyIcon from "xedonium/icons/Copy";
import { useToast } from "@/components/toast";

interface CopyTextProps {
  value: string;
  /** Characters kept from the start. */
  head?: number;
  /** Characters kept from the end (0 for none); the middle is replaced by an ellipsis. */
  tail?: number;
  /** Accessible name of the copy button, e.g. "Copy SHA-256". */
  label: string;
  className?: string;
}

function shorten(value: string, head: number, tail: number): string {
  return value.length <= head + tail + 1 ? value : `${value.slice(0, head)}…${tail > 0 ? value.slice(-tail) : ""}`;
}

/** A shortened value (full text on hover) with a button that copies the whole thing. */
export function CopyText({ value, head = 12, tail = 0, label, className = "" }: CopyTextProps) {
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
    <span className="inline-flex max-w-full items-center gap-1 whitespace-nowrap">
      <code className={`text-xs ${className}`.trim()} title={value}>
        {shorten(value, head, tail)}
      </code>
      <button
        type="button"
        onClick={copy}
        aria-label={label}
        title={copied ? "Copied" : "Copy"}
        className="inline-flex h-5 w-5 shrink-0 items-center justify-center text-app-muted transition hover:text-app-text"
      >
        {copied ? <CheckIcon className="h-3.5 w-3.5" /> : <CopyIcon className="h-3.5 w-3.5" />}
      </button>
    </span>
  );
}
