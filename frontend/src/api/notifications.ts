import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";
import type {
  ChannelInput,
  ChannelRecord,
  ChannelTestResult,
  NotificationEventPage,
} from "./types";

export const channelKeys = {
  all: ["notifications"] as const,
  events: (id: string, page: number) => ["notifications", "events", id, page] as const,
};

export const EVENTS_PAGE_SIZE = 20;

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

export function useChannelEvents(id: string | null, page: number) {
  return useQuery({
    queryKey: channelKeys.events(id ?? "", page),
    queryFn: () =>
      api<NotificationEventPage>(
        `/api/notifications/${id}/events?limit=${EVENTS_PAGE_SIZE}&offset=${page * EVENTS_PAGE_SIZE}`,
      ),
    enabled: id !== null,
    placeholderData: keepPreviousData,
  });
}
