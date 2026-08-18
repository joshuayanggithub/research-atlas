// Import your own library and see it on the map.
//
// The point is coverage: with a reading list active the map shows only the papers you have
// read, so the regions you have worked through and the ones you have never touched become a
// picture rather than a feeling. It is a filter facet, which means the list view, the c-TF-IDF
// topic label and every other facet compose with it for free.
//
// Matching happens once, at import, against the on-demand arXiv index plus the titles already
// in the browser — see data/readingList for why it is tried identifier-first.

import { BookOpen, Upload, X } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { Dataset } from "../data/types";
import { useStore } from "../state/store";
import { loadImportIndex } from "../data/loadArtifacts";
import { usePapersReady } from "../data/usePapersReady";
import { matchReadingList, parseReadingList, type ReadingItem } from "../data/readingList";

export function ReadingListPanel({ ds }: { ds: Dataset }) {
  const readingList = useStore((s) => s.readingList);
  const setReadingList = useStore((s) => s.setReadingList);
  const active = useStore((s) => s.filters.readingLists);
  const setReadingLists = useStore((s) => s.setReadingLists);
  const papersReady = usePapersReady();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  // A library imported before the titles finished streaming matched on identifiers only.
  // Re-match once they land so the title fallback gets its turn — silently, because the user
  // has moved on by then and a jumping count is less confusing than a wrong one.
  const rematched = useRef(false);
  useEffect(() => {
    if (!papersReady || rematched.current || !readingList?.unmatchedItems?.length) return;
    rematched.current = true;
    void (async () => {
      const index = await loadImportIndex().catch(() => null);
      const found = matchReadingList(ds, readingList.unmatchedItems ?? [], index);
      if (found.size === 0) return;
      const nodesByList = { ...readingList.nodesByList };
      const stillUnmatched: ReadingItem[] = [];
      for (const item of readingList.unmatchedItems ?? []) {
        const node = found.get(item.key);
        if (node === undefined) {
          stillUnmatched.push(item);
          continue;
        }
        nodesByList[item.list] = [...(nodesByList[item.list] ?? []), node];
      }
      setReadingList({
        ...readingList,
        nodesByList,
        matched: readingList.matched + found.size,
        unmatchedItems: stillUnmatched,
      });
    })();
  }, [papersReady, readingList, ds, setReadingList]);

  const onFile = async (file: File) => {
    setBusy(true);
    setError(null);
    try {
      const items = parseReadingList(await file.text(), file.name);
      // Only fetched for an actual import; failure is not fatal — titles alone still match a
      // useful fraction, so fall through rather than refusing the file.
      const index = await loadImportIndex().catch(() => null);
      const matches = matchReadingList(ds, items, index);
      const nodesByList: Record<string, number[]> = {};
      const unmatched: ReadingItem[] = [];
      for (const item of items) {
        const node = matches.get(item.key);
        if (node === undefined) {
          unmatched.push(item);
          continue;
        }
        (nodesByList[item.list] ??= []).push(node);
      }
      const lists = Object.keys(nodesByList);
      if (lists.length === 0) {
        setError(`None of the ${items.length} entries are in this corpus (CS/AI arXiv, 1991–2026).`);
        setBusy(false);
        return;
      }
      setReadingList({
        fileName: file.name,
        lists,
        nodesByList,
        total: items.length,
        matched: matches.size,
        unmatchedItems: unmatched,
      });
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      rematched.current = false;
    }
  };

  const toggle = (name: string) =>
    setReadingLists(
      active.includes(name) ? active.filter((n) => n !== name) : [...active, name],
    );

  return (
    <div className="filter-block reading-list" role="region" aria-label="Reading list">
      <h4>
        <BookOpen size={13} aria-hidden="true" /> Reading list
      </h4>

      {!readingList && (
        <p className="subtle small">
          Import your library to see which papers you have read on the map. Zotero, Mendeley and
          Paperpile all export <strong>CSL JSON</strong>; BibTeX works too. For Zotero
          collections with read/unread folders, <code>tools/zotero_export.py</code> keeps the
          folder names.
        </p>
      )}

      <input
        ref={fileRef}
        type="file"
        accept=".json,.bib,.bibtex,application/json,text/plain"
        style={{ display: "none" }}
        onChange={(e) => {
          const f = e.target.files?.[0];
          if (f) void onFile(f);
          e.target.value = ""; // re-importing the same filename must still fire onChange
        }}
      />

      <button
        type="button"
        className="text-btn import-btn"
        disabled={busy}
        onClick={() => fileRef.current?.click()}
      >
        <Upload size={13} aria-hidden="true" />
        {busy ? "Matching…" : readingList ? "Import another file" : "Import a reading list"}
      </button>

      {error && (
        <p className="small warn" role="alert">
          {error}
        </p>
      )}

      {readingList && (
        <>
          <div className="reading-list-summary subtle small">
            <strong>{readingList.matched.toLocaleString()}</strong> of{" "}
            {readingList.total.toLocaleString()} entries matched this corpus
            {readingList.matched < readingList.total && (
              // Say why rather than leaving a silent shortfall: most misses are simply papers
              // outside a CS/AI arXiv corpus (books, non-CS journals, theses).
              <span> — the rest are outside it, or have no arXiv id and no matching title.</span>
            )}
          </div>
          <div className="chip-row">
            {readingList.lists.map((name) => (
              <button
                key={name}
                type="button"
                className={`chip ${active.includes(name) ? "active" : ""}`}
                aria-pressed={active.includes(name)}
                onClick={() => toggle(name)}
              >
                {name}
                <span className="count">
                  {(readingList.nodesByList[name] ?? []).length.toLocaleString()}
                </span>
              </button>
            ))}
          </div>
          <button
            type="button"
            className="text-btn"
            onClick={() => setReadingList(null)}
            aria-label="Remove the imported reading list"
          >
            <X size={12} aria-hidden="true" /> Remove import
          </button>
        </>
      )}
    </div>
  );
}
