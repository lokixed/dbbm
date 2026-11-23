export function formatBytes(size: number): string {
  const units = ["B", "KiB", "MiB", "GiB", "TiB"];
  let value = size;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${unit === 0 ? value : value.toFixed(1)} ${units[unit]}`;
}

export function formatTime(iso: string): string {
  return new Date(iso).toLocaleString();
}

export function formatMs(ms: number): string {
  if (ms < 1000) return `${ms} ms`;
  return formatSeconds(Math.round(ms / 1000));
}

export function formatSeconds(total: number): string {
  if (total < 60) return `${total}s`;
  const minutes = Math.floor(total / 60);
  const seconds = total % 60;
  if (minutes < 60) return `${minutes}m ${seconds}s`;
  return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
}

/** Elapsed time of a run; still counting while it has no end. */
export function formatDuration(start: string | null, end: string | null, now = Date.now()): string {
  if (!start) return "-";
  const finish = end ? new Date(end).getTime() : now;
  return formatSeconds(Math.max(0, Math.round((finish - new Date(start).getTime()) / 1000)));
}
