import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "./client";
import type { AuthStatus, Credentials, User } from "./types";

export const authKeys = {
  status: ["auth", "status"] as const,
  me: ["auth", "me"] as const,
};

export function useAuthStatus() {
  return useQuery({
    queryKey: authKeys.status,
    queryFn: () => api<AuthStatus>("/api/auth/status"),
  });
}

export function useMe() {
  return useQuery({
    queryKey: authKeys.me,
    retry: false,
    queryFn: async (): Promise<User | null> => {
      try {
        return await api<User>("/api/auth/me", { redirectOn401: false });
      } catch (error) {
        if (error instanceof ApiError && error.status === 401) return null;
        throw error;
      }
    },
  });
}

export function useSetup() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (credentials: Credentials) =>
      api<User>("/api/auth/setup", { method: "POST", body: credentials, redirectOn401: false }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: authKeys.status }),
  });
}

export function useLogin() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (credentials: Credentials) =>
      api<User>("/api/auth/login", { method: "POST", body: credentials, redirectOn401: false }),
    onSuccess: (user) => queryClient.setQueryData(authKeys.me, user),
  });
}

export function useLogout() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => api<void>("/api/auth/logout", { method: "POST", redirectOn401: false }),
    // Dropping the cache forces a fresh status probe, which issues a new CSRF cookie.
    onSettled: () => queryClient.clear(),
  });
}

export interface PasswordChange {
  current_password: string;
  new_password: string;
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (input: PasswordChange) => api<User>("/api/auth/change-password", { method: "POST", body: input }),
  });
}

export function useVersion() {
  return useQuery({
    queryKey: ["version"],
    staleTime: Infinity,
    queryFn: () => api<{ version: string }>("/api/version", { redirectOn401: false }),
  });
}
