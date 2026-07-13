import { useEffect } from "react";
import { loadDataset } from "./data/loadArtifacts";
import { useStore } from "./state/store";
import { MapView } from "./map/MapView";
import { OrgFilterPanel } from "./filters/OrgFilterPanel";
import { AuthorFilter } from "./filters/AuthorFilter";
import { DateRangeSlider } from "./filters/DateRangeSlider";
import { DetailsPanel } from "./panels/DetailsPanel";
import { Legend } from "./panels/Legend";
import { SearchBox } from "./panels/SearchBox";

export default function App() {
  const dataset = useStore((s) => s.dataset);
  const loading = useStore((s) => s.loading);
  const error = useStore((s) => s.error);
  const setDataset = useStore((s) => s.setDataset);
  const setError = useStore((s) => s.setError);
  const clearFilters = useStore((s) => s.clearFilters);
  const filters = useStore((s) => s.filters);

  useEffect(() => {
    loadDataset()
      .then(setDataset)
      .catch((e) => setError(String(e)));
  }, [setDataset, setError]);

  if (error) {
    return (
      <div className="fullscreen center">
        <div className="error">
          <h2>Failed to load data</h2>
          <p>{error}</p>
          <p className="subtle">Run the pipeline first: <code>python -m pipeline.run_all</code></p>
        </div>
      </div>
    );
  }

  if (loading || !dataset) {
    return (
      <div className="fullscreen center">
        <div className="loading">Loading the research map…</div>
      </div>
    );
  }

  const m = dataset.manifest;
  const filtersActive =
    filters.orgKeys.length > 0 || filters.authorIds.length > 0;

  return (
    <div className="app">
      <MapView ds={dataset} />

      <header className="topbar">
        <div className="title">
          <strong>Research Visualizer</strong>
          <span className="subtle">
            {m.corpus.count.toLocaleString()} CS papers · {m.corpus.date_from.slice(0, 4)}–
            {m.corpus.date_to.slice(0, 4)} · embeddings: {m.embedding.backend}
          </span>
        </div>
        <SearchBox ds={dataset} />
      </header>

      <aside className="sidebar">
        <div className="sidebar-head">
          <h3>Filters</h3>
          {filtersActive && (
            <button className="text-btn" onClick={clearFilters}>
              clear
            </button>
          )}
        </div>
        <OrgFilterPanel ds={dataset} />
        <AuthorFilter ds={dataset} />
        <DateRangeSlider ds={dataset} />
        <Legend ds={dataset} />
        <div className="hint subtle">
          Scroll to zoom — labels sharpen from fields → topics → subtopics. Click a paper
          for citations & related works.
        </div>
      </aside>

      <DetailsPanel ds={dataset} />
    </div>
  );
}
