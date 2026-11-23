import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type { AppSettings, AppSettingsRecord } from "./types";

const settingsKey = ["settings"] as const;

export function useAppSettings() {
  return useQuery({ queryKey: settingsKey, queryFn: () => api<AppSettingsRecord>("/api/settings") });
}

export function useSaveSettings() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: AppSettings) =>
      api<AppSettingsRecord>("/api/settings", { method: "PUT", body: input }),
    onSuccess: (saved) => {
      queryClient.setQueryData(settingsKey, saved);
      return queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}
