import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import type { SchedulePreview } from "./types";

export function useTimezones() {
  return useQuery({
    queryKey: ["schedule", "timezones"],
    queryFn: () => api<string[]>("/api/schedule/timezones"),
    staleTime: Infinity,
  });
}

/** Next fire times of a cron expression; pass a debounced expression. */
export function useSchedulePreview(cron: string, timezone: string) {
  return useQuery({
    queryKey: ["schedule", "preview", cron, timezone],
    queryFn: () =>
      api<SchedulePreview>("/api/schedule/preview", {
        method: "POST",
        body: { cron, timezone: timezone || null },
      }),
    enabled: cron.trim() !== "",
    retry: false,
    staleTime: 60_000,
  });
}
