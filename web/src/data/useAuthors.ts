// React access to the lazily-loaded author index.
//
// The index is not part of the startup bundle (see loadArtifacts.loadAuthors): it is 829k rows
// and unpacking it measured 2,875 ms, ~50% of load time, despite nothing on first paint using
// it. Components that genuinely need authors pull them through this hook, which triggers the
// fetch on mount and re-renders as chunks land.
//
// Chunks (D32) accumulate into ONE array that is mutated in place — re-copying 829k rows per
// chunk would cost more than the decode. So the hook cannot compare array identity to know
// something changed; it tracks the chunk counter instead and re-reads the same array.

import { useEffect, useState } from "react";
import type { AuthorRow } from "./types";
import { authorsProgress, loadAuthors, onAuthorsChunk, peekAuthors } from "./loadArtifacts";

export function useAuthors(enabled = true): AuthorRow[] {
  const [, setChunks] = useState(authorsProgress);
  useEffect(() => {
    // Gated: unpacking authors is seconds of synchronous main-thread work, so it must not run
    // just because a component mounted. Callers enable it when the user actually needs authors
    // (typing in the author box, opening a paper, expanding a researcher list).
    if (!enabled) return;
    let live = true;
    const off = onAuthorsChunk(() => {
      if (live) setChunks(authorsProgress());
    });
    loadAuthors()
      .then(() => { if (live) setChunks(authorsProgress()); })
      .catch(() => { /* author features degrade to empty; the map still works */ });
    return () => { live = false; off(); };
  }, [enabled]);
  return peekAuthors();
}
