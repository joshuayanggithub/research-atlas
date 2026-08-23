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
  const [rows, setRows] = useState<AuthorRow[]>([]);
  useEffect(() => {
    if (!enabled || query.trim().length === 0) { setRows([]); return; }
    let live = true;
    // Debounced: a chunk fetch per keystroke would be a request per character on a slow link.
    const timer = window.setTimeout(() => {
      void searchAuthors(query)
        .then((r) => { if (live) setRows(r); })
        .catch(() => { if (live) setRows([]); });
    }, 160);
    return () => { live = false; window.clearTimeout(timer); };
  }, [query, enabled]);
  return rows;
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
