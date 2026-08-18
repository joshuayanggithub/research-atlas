// What is still arriving, and how far along it is.
//
// The map paints before its data is complete (D23) — the right call on a ~1 MB/s link, but it
// leaves the interface quietly lying: a paper with no title looks untitled, a search with no
// results looks like it found nothing, and a histogram bar at zero looks like a year with no
// papers. All three are just "not downloaded yet". This says so, and disappears the moment
// everything has landed.

import { useEffect, useState } from "react";
import {
  deferredProgress,
  onAuthorsChunk,
  onEdgesReady,
  onPapersReady,
  onPointTiles,
  type DeferredProgress,
} from "../data/loadArtifacts";

export function LoadingStatus() {
  const [items, setItems] = useState<DeferredProgress[]>(deferredProgress);
  useEffect(() => {
    const refresh = () => setItems(deferredProgress());
    // Every deferred stream already announces its progress; this just listens to all of them.
    const offs = [
      onPointTiles(refresh),
      onAuthorsChunk(refresh),
      onPapersReady(refresh),
      onEdgesReady(refresh),
    ];
    // A slow tick as a backstop: a stream that stalls mid-flight would otherwise leave a stale
    // reading on screen forever.
    const timer = window.setInterval(refresh, 2000);
    return () => {
      for (const off of offs) off();
      window.clearInterval(timer);
    };
  }, []);

  const pending = items.filter((p) => p.loaded < p.total);
  if (pending.length === 0) return null;

  const totalLoaded = items.reduce((n, p) => n + p.loaded, 0);
  const totalAll = items.reduce((n, p) => n + p.total, 0);
  const pct = totalAll > 0 ? Math.round((totalLoaded / totalAll) * 100) : 100;

  return (
    <div className="loading-status" role="status" aria-live="polite">
      <span className="loading-spinner" aria-hidden="true" />
      <span className="loading-status-text">
        loading {pending.map((p) => p.label).join(" · ")}
        <span className="subtle"> {pct}%</span>
      </span>
    </div>
  );
}
