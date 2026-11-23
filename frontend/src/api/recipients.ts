import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

/** An age public key that backups can be encrypted to. Only public keys are ever stored. */
export interface Recipient {
  id: string;
  name: string;
  public_key: string;
  fingerprint: string;
  created_at: string;
  /** Names of the jobs that encrypt to this key. */
  used_by: string[];
}

export const recipientKeys = { all: ["recipients"] as const };

export function useRecipients() {
  return useQuery({
    queryKey: recipientKeys.all,
    queryFn: () => api<Recipient[]>("/api/recipients"),
  });
}

export function useAddRecipient() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: { name: string; public_key: string }) =>
      api<Recipient>("/api/recipients", { method: "POST", body: input }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: recipientKeys.all }),
  });
}

export function useDeleteRecipient() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/api/recipients/${id}`, { method: "DELETE" }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: recipientKeys.all }),
  });
}
