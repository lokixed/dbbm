export const CHANNEL_TYPE_NAME = { slack: "Slack", email: "Email", webhook: "Webhook" } as const;
export type ChannelTypeKey = keyof typeof CHANNEL_TYPE_NAME;
