import type { SecretsInput } from "@/api/types";

/** What the user did to a stored (write-only) secret in the form. */
export type SecretState =
  | { action: "keep" }
  | { action: "clear" }
  | { action: "set"; value: string };

export function initialSecret(isSet: boolean): SecretState {
  return isSet ? { action: "keep" } : { action: "set", value: "" };
}

/** undefined means "omit the key", so the server keeps its stored value. */
export function secretPayload(state: SecretState): string | null | undefined {
  if (state.action === "clear") return null;
  if (state.action === "set" && state.value !== "") return state.value;
  return undefined;
}

export function buildSecrets(states: Record<string, SecretState>): SecretsInput {
  const result: SecretsInput = {};
  for (const [field, state] of Object.entries(states)) {
    const payload = secretPayload(state);
    if (payload !== undefined) result[field] = payload;
  }
  return result;
}
