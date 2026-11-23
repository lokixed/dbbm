import type { StepResults } from "@/api/types";

/** True when a partial run has a failed upload whose dump succeeded, i.e. something to retry. */
export function hasRetryableFailures(results: StepResults): boolean {
  return (results.units ?? []).some(
    (u) => u.dump.status === "ok" && u.destinations.some((d) => d.status === "failed" && d.path),
  );
}
