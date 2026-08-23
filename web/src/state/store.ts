// Global app state (zustand). Holds the loaded dataset, the current filters, the
// selection, and view options. Filter *evaluation* (into a GPU mask) lives in
// filters/useFilterMask; this store only holds the intent.

import { create } from "zustand";
import type { Dataset, Label } from "../data/types";
import type { ReadingItem } from "../data/readingList";

export type ColorMode = "subfield" | "org" | "recency";
export type OrgDisplayMode = "dim" | "hide";
export type EdgeMode = "out" | "in" | "both";

export interface LabelFocus {
  id: number;
  x: number;
  y: number;
  level: number;
  // Incrementing this lets selecting the same label again retrigger map navigation.
  requestId: number;
}

export interface Filters {
  // Date range as month indices (months since corpus start month). monthMin/monthMax are
  // the source of truth for filtering; yearMin/yearMax are kept in sync for coarse display
  // and the recency color ramp.
  monthMin: number;
  monthMax: number;
  yearMin: number;
  yearMax: number;
  orgKeys: string[]; // selected org keys (union)
  authorIds: number[]; // selected local author ids (union)
  // CS-topic filter (the corpus taxonomy). A paper passes if its subfield is in subfieldIds
  // (when any selected) AND its topic is in topicIds (when any selected). Empty = no topic
  // filter. Composes with org/author/date like the other facets.
  subfieldIds: number[];
  topicIds: number[];
  // Map regions selected by clicking their label. Membership is computed client-side from the
  // label centroids (filters/labelMembership), so this needs no pipeline artifact.
  labelIds: number[];
  // Citation-count range. `citeMax: null` means "no upper bound" — kept distinct from the
  // corpus maximum so the filter stays correct if a rebuild changes the most-cited paper.
  citeMin: number;
  citeMax: number | null;
  // Names of the imported reading lists currently being shown (see data/readingList). Empty
  // means no reading-list filter, which is different from "an imported list that matched
  // nothing" — that one legitimately shows an empty map.
  readingLists: string[];
}

/** An imported library, resolved against the corpus. Held outside `filters` because it is
 *  data, not a selection: the selection is which of its lists are active. */
export interface ReadingListState {
  fileName: string;
  lists: string[];
  /** node ids by list name, already matched to this corpus. */
  nodesByList: Record<string, number[]>;
  total: number;
  matched: number;
  /** Entries that matched nothing yet. Kept so the import can be retried once titles finish
   *  streaming — an import done in the first seconds only had identifiers to work with. */
  unmatchedItems?: ReadingItem[];
}

interface AppState {
  dataset: Dataset | null;
  loading: boolean;
  error: string | null;

  // view
  colorMode: ColorMode;
  orgDisplayMode: OrgDisplayMode;
  edgeMode: EdgeMode;
  showCitationEdges: boolean;
  currentZoom: number;

  // interaction
  selectedNode: number | null;
  hoverNode: number | null;
  focusedLabel: LabelFocus | null;
  // When a paper is selected, hide connected papers below this relevance PERCENTILE (0 = show
  // the whole citation network, 1 = only the most relevant). Set on each selection by
  // autoRelevanceThreshold: 0 for ordinary papers, pre-opened for hubs whose network would
  // otherwise bury the map.
  relevanceThreshold: number;

  filters: Filters;
  /** The imported library, or null. Survives reloads via localStorage (see setReadingList). */
  readingList: ReadingListState | null;

  setDataset: (d: Dataset) => void;
  setError: (e: string) => void;
  setColorMode: (m: ColorMode) => void;
  setOrgDisplayMode: (m: OrgDisplayMode) => void;
  setEdgeMode: (m: EdgeMode) => void;
  setShowCitationEdges: (visible: boolean) => void;
  setZoom: (z: number) => void;
  selectNode: (id: number | null) => void;
  focusLabel: (label: Label) => void;
  setHover: (id: number | null) => void;
  setRelevanceThreshold: (t: number) => void;
  /** Re-apply the automatic threshold once a paper's full network is known. */
  syncAutoRelevance: (node: number) => void;
  /** True once the user has moved the relevance slider for this selection. */
  relevanceTouched: boolean;
  setYearRange: (min: number, max: number) => void;
  setMonthRange: (min: number, max: number) => void;
  toggleOrg: (key: string) => void;
  setAuthors: (ids: number[]) => void;
  setCitationRange: (min: number, max: number | null) => void;
  setSubfieldIds: (ids: number[]) => void;
  setLabelIds: (ids: number[]) => void;
  toggleLabel: (id: number) => void;
  setTopicIds: (ids: number[]) => void;
  setReadingList: (rl: ReadingListState | null) => void;
  setReadingLists: (names: string[]) => void;
  clearFilters: () => void;
}

// localStorage key for the imported library. Versioned: a change to what we persist should
// invalidate old entries rather than half-load them.
export const READING_LIST_KEY = "research-atlas.reading-list.v1";

const DEFAULT_FILTERS: Filters = {
  monthMin: 0,
  monthMax: 83, // 7 years (2020-01 .. 2026-12) as a fallback; overwritten from manifest
  yearMin: 2020,
  yearMax: 2026,
  orgKeys: [],
  authorIds: [],
  labelIds: [],
  subfieldIds: [],
  topicIds: [],
  readingLists: [],
  citeMin: 0,
  citeMax: null,
};

// Full date range as month indices for a dataset. monthMax is the last covered month.
function datasetMonthRange(d: Dataset): { min: number; max: number; yearMin: number; yearMax: number } {
  const yearMin = parseInt(d.manifest.corpus.date_from.slice(0, 4));
  const yearMax = parseInt(d.manifest.corpus.date_to.slice(0, 4));
  const toMonth = parseInt(d.manifest.corpus.date_to.slice(5, 7)) || 12;
  return { min: 0, max: (yearMax - yearMin) * 12 + (toMonth - 1), yearMin, yearMax };
}

// Most papers a selection can show at once before the map stops being legible. Chosen to sit
// comfortably above a typical network (which opens fully) and well below a hub's tens of
// thousands.
const NETWORK_SOFT_CAP = 1500;

/**
 * Starting relevance threshold for a newly selected paper, as a percentile in [0,1].
 *
 * 0 means "show the whole network", which is right for the vast majority of papers. Only when a
 * network exceeds NETWORK_SOFT_CAP is the slider pre-opened, and only far enough to leave roughly
 * that many of the most relevant papers visible — the user can always drag it back to "all".
 */
function autoRelevanceThreshold(ds: Dataset | null, node: number): number {
  if (!ds) return 0;
  const total = (ds.citesOut.get(node)?.length ?? 0) + (ds.citedBy.get(node)?.length ?? 0);
  if (total <= NETWORK_SOFT_CAP) return 0;
  return Math.min(0.99, 1 - NETWORK_SOFT_CAP / total);
}

/**
 * A previously imported library, if it still refers to this corpus.
 *
 * Node ids are positions in the current build, so a rebuild silently renumbers everything —
 * a stale list would highlight arbitrary papers rather than the ones you read. The stored copy
 * therefore records the corpus size it was matched against and is dropped when that changes,
 * which is a cheap proxy for "same build" and fails safe: the user re-imports the file.
 *
 * The lists are restored INACTIVE. Opening onto a filtered map that the user did not ask for
 * this session reads as a broken map; one click on the chip brings the view back.
 */
function restoreReadingList(corpusCount: number): ReadingListState | null {
  try {
    const raw = localStorage.getItem(READING_LIST_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as ReadingListState & { corpusCount?: number };
    if (parsed.corpusCount !== corpusCount) {
      localStorage.removeItem(READING_LIST_KEY);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

export const useStore = create<AppState>((set) => ({
  dataset: null,
  loading: true,
  error: null,

  colorMode: "subfield",
  orgDisplayMode: "dim",
  edgeMode: "both",
  showCitationEdges: true,
  currentZoom: -3,

  selectedNode: null,
  hoverNode: null,
  focusedLabel: null,
  relevanceThreshold: 0,
  relevanceTouched: false,

  filters: { ...DEFAULT_FILTERS },
  readingList: null,

  setDataset: (d) => {
    const r = datasetMonthRange(d);
    return set({
      dataset: d,
      loading: false,
      readingList: restoreReadingList(d.points.count),
      filters: {
        ...DEFAULT_FILTERS,
        monthMin: r.min,
        monthMax: r.max,
        yearMin: r.yearMin,
        yearMax: r.yearMax,
      },
    });
  },
  setError: (e) => set({ error: e, loading: false }),
  setColorMode: (m) => set({ colorMode: m }),
  setOrgDisplayMode: (m) => set({ orgDisplayMode: m }),
  setEdgeMode: (m) => set({ edgeMode: m }),
  setShowCitationEdges: (visible) => set({ showCitationEdges: visible }),
  setZoom: (z) => set({ currentZoom: z }),
  // Reset the relevance threshold on every selection change so a new paper always starts
  // showing its whole citation network.
  selectNode: (id) =>
    set((st) => ({
      selectedNode: id,
      focusedLabel: null,
      // Open the relevance filter far enough to show a READABLE slice of the network, not all
      // of it. Selecting "Attention Is All You Need" reveals its 69,262 citers as points, which
      // covers the entire map in one colour and hides the very structure the selection was meant
      // to expose. The slider was the intended remedy but started at "all", so the first thing a
      // user saw was the flood. Small networks are unaffected and still open fully.
      relevanceThreshold: id === null ? 0 : autoRelevanceThreshold(st.dataset, id),
      // The threshold above is computed from whatever of the graph is resident. At selection
      // time that is the zoom tiers, which for a hub is a small fraction of its network — so
      // this has to be recomputed once the paper's own shard lands (syncAutoRelevance), unless
      // the user has meanwhile moved the slider themselves.
      relevanceTouched: false,
    })),
  /** Recompute the auto threshold now that `node`'s complete network is known. */
  syncAutoRelevance: (node) =>
    set((st) => {
      if (st.relevanceTouched || st.selectedNode !== node) return {};
      const next = autoRelevanceThreshold(st.dataset, node);
      return next === st.relevanceThreshold ? {} : { relevanceThreshold: next };
    }),
  focusLabel: (label) =>
    set((s) => ({
      selectedNode: null,
      relevanceThreshold: 0,
      relevanceTouched: false,
      focusedLabel: {
        id: label.id,
        x: label.x,
        y: label.y,
        level: label.level,
        requestId: (s.focusedLabel?.requestId ?? 0) + 1,
      },
    })),
  setHover: (id) => set({ hoverNode: id }),
  setRelevanceThreshold: (t) =>
    set({ relevanceThreshold: Math.max(0, Math.min(1, t)), relevanceTouched: true }),
  setYearRange: (min, max) =>
    set((s) => {
      const yearMin = s.dataset ? parseInt(s.dataset.manifest.corpus.date_from.slice(0, 4)) : 2020;
      const toMonth =
        s.dataset ? parseInt(s.dataset.manifest.corpus.date_to.slice(5, 7)) || 12 : 12;
      // Map a whole-year range to month indices: Jan of min .. Dec (or last month) of max.
      const monthMin = (min - yearMin) * 12;
      const isLastYear = s.dataset
        ? max === parseInt(s.dataset.manifest.corpus.date_to.slice(0, 4))
        : false;
      const monthMax = (max - yearMin) * 12 + (isLastYear ? toMonth - 1 : 11);
      return { filters: { ...s.filters, yearMin: min, yearMax: max, monthMin, monthMax } };
    }),
  setMonthRange: (min, max) =>
    set((s) => {
      const yearMin = s.dataset ? parseInt(s.dataset.manifest.corpus.date_from.slice(0, 4)) : 2020;
      return {
        filters: {
          ...s.filters,
          monthMin: min,
          monthMax: max,
          // Keep coarse year bounds in sync for the recency ramp + summary text.
          yearMin: yearMin + Math.floor(min / 12),
          yearMax: yearMin + Math.floor(max / 12),
        },
      };
    }),
  toggleOrg: (key) =>
    set((s) => {
      const has = s.filters.orgKeys.includes(key);
      return {
        filters: {
          ...s.filters,
          orgKeys: has
            ? s.filters.orgKeys.filter((k) => k !== key)
            : [...s.filters.orgKeys, key],
        },
      };
    }),
  setAuthors: (ids) => set((s) => ({ filters: { ...s.filters, authorIds: ids } })),
  setCitationRange: (min, max) =>
    set((s) => ({ filters: { ...s.filters, citeMin: min, citeMax: max } })),
  setSubfieldIds: (ids) => set((s) => ({ filters: { ...s.filters, subfieldIds: ids } })),
  setReadingList: (rl) =>
    set((s) => {
      // Persist the MATCHED node ids, not the source file: they are small, already resolved,
      // and re-matching on every reload would mean re-fetching the import index. A corpus
      // rebuild renumbers nodes, so the stored copy carries the corpus size it was matched
      // against and is discarded when that no longer agrees (see App's restore).
      try {
        if (rl) {
          localStorage.setItem(
            READING_LIST_KEY,
            JSON.stringify({ ...rl, corpusCount: s.dataset?.points.count ?? 0 }),
          );
        } else {
          localStorage.removeItem(READING_LIST_KEY);
        }
      } catch {
        /* private mode / quota — the list still works for this session */
      }
      return {
        readingList: rl,
        filters: { ...s.filters, readingLists: rl ? rl.lists : [] },
      };
    }),
  setReadingLists: (names) => set((s) => ({ filters: { ...s.filters, readingLists: names } })),
  setLabelIds: (ids) => set((s) => ({ filters: { ...s.filters, labelIds: ids } })),
  toggleLabel: (id) =>
    set((s) => ({
      filters: {
        ...s.filters,
        labelIds: s.filters.labelIds.includes(id)
          ? s.filters.labelIds.filter((x) => x !== id)
          : [...s.filters.labelIds, id],
      },
    })),
  setTopicIds: (ids) => set((s) => ({ filters: { ...s.filters, topicIds: ids } })),
  clearFilters: () =>
    set((s) => {
      const r = s.dataset ? datasetMonthRange(s.dataset) : null;
      return {
        filters: {
          ...DEFAULT_FILTERS,
          monthMin: r ? r.min : DEFAULT_FILTERS.monthMin,
          monthMax: r ? r.max : DEFAULT_FILTERS.monthMax,
          yearMin: r ? r.yearMin : DEFAULT_FILTERS.yearMin,
          yearMax: r ? r.yearMax : DEFAULT_FILTERS.yearMax,
        },
      };
    }),
}));
