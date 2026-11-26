import { useChannels } from "@/api/notifications";
import { useDestinations } from "@/api/destinations";
import { useJobs } from "@/api/jobs";
import { useSources } from "@/api/sources";
import type { ChannelTypeKey } from "@/lib/channelType";

// Pages that only get an id and a name (a run, an artifact, a notification) look the type up in the
// lists the app already caches, so each can show the right icon. Until a list loads, or when the
// record was deleted, the lookup returns undefined and the icon falls back to a neutral one.

export function useJobSourceType(): (jobId: string | null) => string | undefined {
  const jobs = useJobs();
  return (jobId) => (jobId ? jobs.data?.find((job) => job.id === jobId)?.source.type : undefined);
}

export function useDestinationType(): (destinationId: string | null) => string | undefined {
  const destinations = useDestinations();
  return (id) => (id ? destinations.data?.find((d) => d.id === id)?.type : undefined);
}

export function useChannelType(): (channelId: string | null) => ChannelTypeKey | undefined {
  const channels = useChannels();
  return (id) => (id ? channels.data?.find((c) => c.id === id)?.type : undefined);
}

export function useSourceType(): (sourceId: string | null) => string | undefined {
  const sources = useSources();
  return (id) => (id ? sources.data?.find((source) => source.id === id)?.type : undefined);
}
