// Global app state (zustand). Holds the loaded dataset, the current filters, the
// selection, and view options. Filter *evaluation* (into a GPU mask) lives in
// filters/useFilterMask; this store only holds the intent.

import { create } from "zustand";
import type { Dataset } from "../data/types";

export type ColorMode = "subfield" | "org" | "recency";
export type OrgDisplayMode = "dim" | "hide";
export type EdgeMode = "out" | "in" | "both";

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
  // CS-topic filter (OpenAlex taxonomy). A paper passes if its subfield is in subfieldIds
  // (when any selected) AND its topic is in topicIds (when any selected). Empty = no topic
  // filter. Composes with org/author/date like the other facets.
  subfieldIds: number[];
  topicIds: number[];
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
  // When a paper is selected, hide connected papers whose Connected-Papers relevance score is
  // below this threshold (0 = show the whole citation network, 1 = only the most relevant).
  // Reset to 0 whenever the selection changes.
  relevanceThreshold: number;

  filters: Filters;

  setDataset: (d: Dataset) => void;
  setError: (e: string) => void;
  setColorMode: (m: ColorMode) => void;
  setOrgDisplayMode: (m: OrgDisplayMode) => void;
  setEdgeMode: (m: EdgeMode) => void;
  setShowCitationEdges: (visible: boolean) => void;
  setZoom: (z: number) => void;
  selectNode: (id: number | null) => void;
  setHover: (id: number | null) => void;
  setRelevanceThreshold: (t: number) => void;
  setYearRange: (min: number, max: number) => void;
  setMonthRange: (min: number, max: number) => void;
  toggleOrg: (key: string) => void;
  setAuthors: (ids: number[]) => void;
  setSubfieldIds: (ids: number[]) => void;
  setTopicIds: (ids: number[]) => void;
  clearFilters: () => void;
}

const DEFAULT_FILTERS: Filters = {
  monthMin: 0,
  monthMax: 83, // 7 years (2020-01 .. 2026-12) as a fallback; overwritten from manifest
  yearMin: 2020,
  yearMax: 2026,
  orgKeys: [],
  authorIds: [],
  subfieldIds: [],
  topicIds: [],
};

// Full date range as month indices for a dataset. monthMax is the last covered month.
function datasetMonthRange(d: Dataset): { min: number; max: number; yearMin: number; yearMax: number } {
  const yearMin = parseInt(d.manifest.corpus.date_from.slice(0, 4));
  const yearMax = parseInt(d.manifest.corpus.date_to.slice(0, 4));
  const toMonth = parseInt(d.manifest.corpus.date_to.slice(5, 7)) || 12;
  return { min: 0, max: (yearMax - yearMin) * 12 + (toMonth - 1), yearMin, yearMax };
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
  relevanceThreshold: 0,

  filters: { ...DEFAULT_FILTERS },

  setDataset: (d) => {
    const r = datasetMonthRange(d);
    return set({
      dataset: d,
      loading: false,
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
  selectNode: (id) => set({ selectedNode: id, relevanceThreshold: 0 }),
  setHover: (id) => set({ hoverNode: id }),
  setRelevanceThreshold: (t) => set({ relevanceThreshold: Math.max(0, Math.min(1, t)) }),
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
  setSubfieldIds: (ids) => set((s) => ({ filters: { ...s.filters, subfieldIds: ids } })),
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
