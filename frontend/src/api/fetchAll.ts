/** The API's largest page; a bigger `limit` is rejected. */
export const MAX_API_PAGE = 200;
/** A safety cap so a huge table cannot be pulled into the browser in one go. */
export const MAX_ROWS = 10000;

interface Page<T> {
  items: T[];
  total: number;
}

/** Reads every page matching the filters (up to MAX_ROWS), so the data grid can sort and page them itself. */
export async function fetchAllPages<T, P extends Page<T>>(load: (offset: number) => Promise<P>): Promise<P> {
  const first = await load(0);
  const items = [...first.items];
  while (items.length < Math.min(first.total, MAX_ROWS)) {
    const next = await load(items.length);
    if (next.items.length === 0) break;
    items.push(...next.items);
  }
  return { ...first, items };
}
