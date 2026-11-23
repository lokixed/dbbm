import { useEffect, useRef, useState } from "react";
import { fetchRunLog } from "@/api/runs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

type Connection = "connecting" | "live" | "reconnecting" | "ended" | "unavailable";

const CONNECTION_LABEL: Record<Connection, string> = {
  connecting: "Connecting...",
  live: "Live",
  reconnecting: "Reconnecting...",
  ended: "Finished",
  unavailable: "Live stream unavailable - showing the saved log",
};

const FLUSH_MS = 100;
const BOTTOM_TOLERANCE_PX = 24;

function toLines(text: string): string[] {
  const lines = text.split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  return lines;
}

function lineClass(line: string): string {
  if (line.includes("[ERROR]")) return "text-destructive";
  if (line.includes("[WARN]")) return "text-amber-600 dark:text-amber-400";
  return "";
}

interface LogViewerProps {
  runId: string;
  /** Called once when the stream reports the run has finished. */
  onDone?: () => void;
}

/** Live log of a run. Mount with `key={runId}` so a new run starts with a clean state. */
export function LogViewer({ runId, onDone }: LogViewerProps) {
  const [lines, setLines] = useState<string[]>([]);
  const [connection, setConnection] = useState<Connection>("connecting");
  const [following, setFollowing] = useState(true);
  const boxRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);
  const onDoneRef = useRef(onDone);

  useEffect(() => {
    onDoneRef.current = onDone;
  }, [onDone]);

  useEffect(() => {
    // EventSource sends the session cookie to the same origin and resumes with Last-Event-ID.
    const source = new EventSource(`/api/runs/${encodeURIComponent(runId)}/logs`);
    let buffer: string[] = [];
    let timer: number | undefined;

    const flush = () => {
      timer = undefined;
      if (buffer.length === 0) return;
      const chunk = buffer;
      buffer = [];
      setLines((current) => current.concat(chunk));
    };

    const loadSavedLog = (finished: boolean) => {
      fetchRunLog(runId)
        .then((text) => setLines(toLines(text)))
        .catch(() => undefined)
        .finally(() => {
          setConnection(finished ? "ended" : "unavailable");
          if (finished) onDoneRef.current?.();
        });
    };

    source.onopen = () => setConnection("live");
    source.onmessage = (event: MessageEvent<string>) => {
      buffer.push(event.data);
      timer ??= window.setTimeout(flush, FLUSH_MS);
    };
    source.addEventListener("done", () => {
      source.close();
      flush();
      loadSavedLog(true);
    });
    source.onerror = () => {
      if (source.readyState === EventSource.CLOSED) {
        flush();
        loadSavedLog(false);
      } else {
        setConnection("reconnecting");
      }
    };

    return () => {
      source.close();
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [runId]);

  useEffect(() => {
    const box = boxRef.current;
    if (box && followRef.current) box.scrollTop = box.scrollHeight;
  }, [lines]);

  const onScroll = () => {
    const box = boxRef.current;
    if (!box) return;
    const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < BOTTOM_TOLERANCE_PX;
    followRef.current = atBottom;
    setFollowing(atBottom);
  };

  const resume = () => {
    const box = boxRef.current;
    if (box) box.scrollTop = box.scrollHeight;
    followRef.current = true;
    setFollowing(true);
  };

  return (
    <div className="space-y-2">
      <div className="flex items-center justify-between gap-3">
        <Badge variant={connection === "live" ? "success" : connection === "unavailable" ? "warning" : "neutral"}>
          {CONNECTION_LABEL[connection]}
        </Badge>
        {!following && (
          <Button type="button" size="sm" variant="secondary" onClick={resume}>
            Paused - jump to latest
          </Button>
        )}
      </div>
      <div
        ref={boxRef}
        onScroll={onScroll}
        role="log"
        aria-label="Run log"
        tabIndex={0}
        className="h-96 overflow-auto rounded-md border bg-muted/40 p-3 font-mono text-xs leading-5"
      >
        {lines.length === 0 ? (
          <p className="text-muted-foreground">Waiting for log output...</p>
        ) : (
          lines.map((line, index) => (
            <div key={index} className={cn("whitespace-pre-wrap break-words", lineClass(line))}>
              {line}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
