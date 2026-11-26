/** True when the query is empty or any of the texts contains it, ignoring case. */
export function matchesSearch(query: string, texts: (string | null | undefined)[]): boolean {
  const needle = query.trim().toLowerCase();
  return needle === "" || texts.some((text) => (text ?? "").toLowerCase().includes(needle));
}
