import { useEffect, useMemo, useRef, useState } from "react";
import Badge, { type BadgeColor } from "xedonium/Badge";
import XLogViewer, { type LogEntry } from "xedonium/LogViewer";
import { fetchRunLog } from "@/api/runs";

type Connection = "connecting" | "live" | "reconnecting" | "ended" | "unavailable";

const CONNECTION_LABEL: Record<Connection, string> = {
  connecting: "Connecting...",
  live: "Live",
  reconnecting: "Reconnecting...",
  ended: "Finished",
  unavailable: "Live stream unavailable - showing the saved log",
};

const CONNECTION_COLOR: Record<Connection, BadgeColor> = {
  connecting: "secondary",
  live: "success",
  reconnecting: "secondary",
  ended: "secondary",
  unavailable: "warning",
};

const FLUSH_MS = 100;

function toLines(text: string): string[] {
  const lines = text.split("\n");
  if (lines[lines.length - 1] === "") lines.pop();
  return lines;
}

function toEntry(line: string, index: number): LogEntry {
  const level = line.includes("[ERROR]") ? "error" : line.includes("[WARN]") ? "warn" : "info";
  return { id: index, level, message: line };
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

  const entries = useMemo(() => lines.map(toEntry), [lines]);

  return (
    <div className="log-compact">
    <XLogViewer
      logs={entries}
      label="Run log"
      height={384}
      autoScroll={following}
      onAutoScrollChange={setFollowing}
      emptyText="Waiting for log output..."
      toolbarStart={
        // The toolbar's other controls are 38px tall; stretch the status badge to match.
        <div className="inline-flex h-[38px] [&_span]:inline-flex [&_span]:h-full [&_span]:items-center [&_span]:text-xs [&_span_span]:px-3">
          <Badge badgeContent={CONNECTION_LABEL[connection]} color={CONNECTION_COLOR[connection]} />
        </div>
      }
    />
    </div>
  );
}
