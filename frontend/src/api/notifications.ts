import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import { fetchAllPages, MAX_API_PAGE } from "./fetchAll";
import type {
  ChannelInput,
  ChannelRecord,
  ChannelTestResult,
  NotificationEventPage,
} from "./types";

export const channelKeys = {
  all: ["notifications"] as const,
  events: (id: string) => ["notifications", "events", id] as const,
};

export function useChannels() {
  return useQuery({ queryKey: channelKeys.all, queryFn: () => api<ChannelRecord[]>("/api/notifications") });
}

function useInvalidate() {
  const queryClient = useQueryClient();
  return () =>
    Promise.all([
      queryClient.invalidateQueries({ queryKey: channelKeys.all }),
      queryClient.invalidateQueries({ queryKey: ["jobs"] }),
    ]);
}

export function useSaveChannel() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: ({ id, input }: { id: string | null; input: ChannelInput }) =>
      id === null
        ? api<ChannelRecord>("/api/notifications", { method: "POST", body: input })
        : api<ChannelRecord>(`/api/notifications/${id}`, { method: "PUT", body: input }),
    onSuccess: invalidate,
  });
}

export function useDeleteChannel() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/api/notifications/${id}`, { method: "DELETE" }),
    onSuccess: invalidate,
  });
}

export function useTestChannel() {
  const invalidate = useInvalidate();
  return useMutation({
    mutationFn: (id: string) =>
      api<ChannelTestResult>(`/api/notifications/${id}/test`, { method: "POST" }),
    onSettled: invalidate,
  });
}

/** Sends a test message with the settings in a form, without saving them. `id` is the saved channel
 * being edited, if any: its stored secrets fill in any secret the form leaves unchanged. */
export function useTestChannelDraft() {
  return useMutation({
    mutationFn: ({ id, input }: { id: string | null; input: ChannelInput }) =>
      api<ChannelTestResult>(`/api/notifications/test${id ? `?channel_id=${encodeURIComponent(id)}` : ""}`, {
        method: "POST",
        body: input,
      }),
  });
}

export function useChannelEvents(id: string | null) {
  return useQuery({
    queryKey: channelKeys.events(id ?? ""),
    queryFn: () =>
      fetchAllPages((offset) =>
        api<NotificationEventPage>(`/api/notifications/${id}/events?limit=${MAX_API_PAGE}&offset=${offset}`),
      ),
    enabled: id !== null,
    placeholderData: keepPreviousData,
  });
}
