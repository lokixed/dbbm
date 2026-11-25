const DESTINATION_TYPE_NAME: Record<string, string> = {
  local: "Local drive",
  gdrive: "Google Drive",
};

export function destinationTypeName(type: string | null | undefined): string {
  return (type && DESTINATION_TYPE_NAME[type]) || "Destination";
}
