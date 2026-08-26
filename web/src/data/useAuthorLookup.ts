// React access to the author index, replacing the whole-list load.
//
// authors.arrow used to be fetched in full (~14.4 MB) so components could scan it. Nothing
// scans now: a type-ahead asks the token index, and anything showing a specific author asks
// for that author's record.

import { useEffect, useState } from "react";
import type { AuthorRow } from "./types";
import {
  ensureAuthorInfo, onAuthorInfo, peekAuthorInfo, searchAuthors,
} from "./loadArtifacts";

/** Type-ahead results for `query`. Empty until the index chunk lands. */
export function useAuthorSearch(query: string, enabled: boolean): AuthorRow[] {
  return useAuthorSearchState(query, enabled).rows;
}

/** Results plus whether the lookup is still in flight. An empty list means "no such author"
 *  ONLY once `pending` is false — the index is a network fetch (D59). */
export function useAuthorSearchState(
  query: string,
  enabled: boolean,
): { rows: AuthorRow[]; pending: boolean } {
  const [state, setState] = useState<{ rows: AuthorRow[]; pending: boolean }>(
    { rows: [], pending: false },
  );
  useEffect(() => {
    if (!enabled || query.trim().length === 0) { setState({ rows: [], pending: false }); return; }
    let live = true;
    setState((prev) => ({ rows: prev.rows, pending: true }));
    // Debounced: a chunk fetch per keystroke would be a request per character on a slow link.
    const timer = window.setTimeout(() => {
      void searchAuthors(query)
        .then((r) => { if (live) setState({ rows: r, pending: false }); })
        .catch(() => { if (live) setState({ rows: [], pending: false }); });
    }, 160);
    return () => { live = false; window.clearTimeout(timer); };
  }, [query, enabled]);
  return state;
}

/** Records for specific authors (name, count, and every row sharing the name). */
export function useAuthorInfo(
  ids: number[],
): Map<number, AuthorRow & { sameNameIds?: number[]; sameNamePapers?: number }> {
  const [, setTick] = useState(0);
  useEffect(() => onAuthorInfo(() => setTick((n) => n + 1)), []);
  const key = ids.join(",");
  useEffect(() => {
    if (ids.length) void ensureAuthorInfo(ids);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
  const out = new Map<number, AuthorRow & { sameNameIds?: number[]; sameNamePapers?: number }>();
  for (const id of ids) {
    const row = peekAuthorInfo(id);
    if (row) out.set(id, row);
  }
  return out;
}
