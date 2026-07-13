// Global app state (zustand). Holds the loaded dataset, the current filters, the
// selection, and view options. Filter *evaluation* (into a GPU mask) lives in
// filters/useFilterMask; this store only holds the intent.

import { create } from "zustand";
import type { Dataset } from "../data/types";

export type ColorMode = "subfield" | "org" | "recency";
export type OrgDisplayMode = "dim" | "hide";
export type EdgeMode = "out" | "in" | "both";

export interface Filters {
  yearMin: number;
  yearMax: number;
  orgKeys: string[]; // selected org keys (union)
  authorIds: number[]; // selected local author ids (union)
}

interface AppState {
  dataset: Dataset | null;
  loading: boolean;
  error: string | null;

  // view
  colorMode: ColorMode;
  orgDisplayMode: OrgDisplayMode;
  edgeMode: EdgeMode;
  currentZoom: number;

  // interaction
  selectedNode: number | null;
  hoverNode: number | null;

  filters: Filters;

  setDataset: (d: Dataset) => void;
  setError: (e: string) => void;
  setColorMode: (m: ColorMode) => void;
  setOrgDisplayMode: (m: OrgDisplayMode) => void;
  setEdgeMode: (m: EdgeMode) => void;
  setZoom: (z: number) => void;
  selectNode: (id: number | null) => void;
  setHover: (id: number | null) => void;
  setYearRange: (min: number, max: number) => void;
  toggleOrg: (key: string) => void;
  setAuthors: (ids: number[]) => void;
  clearFilters: () => void;
}

const DEFAULT_FILTERS: Filters = {
  yearMin: 2020,
  yearMax: 2026,
  orgKeys: [],
  authorIds: [],
};

export const useStore = create<AppState>((set) => ({
  dataset: null,
  loading: true,
  error: null,

  colorMode: "subfield",
  orgDisplayMode: "dim",
  edgeMode: "both",
  currentZoom: -3,

  selectedNode: null,
  hoverNode: null,

  filters: { ...DEFAULT_FILTERS },

  setDataset: (d) =>
    set({
      dataset: d,
      loading: false,
      filters: {
        ...DEFAULT_FILTERS,
        yearMin: parseInt(d.manifest.corpus.date_from.slice(0, 4)),
        yearMax: parseInt(d.manifest.corpus.date_to.slice(0, 4)),
      },
    }),
  setError: (e) => set({ error: e, loading: false }),
  setColorMode: (m) => set({ colorMode: m }),
  setOrgDisplayMode: (m) => set({ orgDisplayMode: m }),
  setEdgeMode: (m) => set({ edgeMode: m }),
  setZoom: (z) => set({ currentZoom: z }),
  selectNode: (id) => set({ selectedNode: id }),
  setHover: (id) => set({ hoverNode: id }),
  setYearRange: (min, max) =>
    set((s) => ({ filters: { ...s.filters, yearMin: min, yearMax: max } })),
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
  clearFilters: () =>
    set((s) => ({
      filters: {
        ...DEFAULT_FILTERS,
        yearMin: s.dataset
          ? parseInt(s.dataset.manifest.corpus.date_from.slice(0, 4))
          : 2020,
        yearMax: s.dataset
          ? parseInt(s.dataset.manifest.corpus.date_to.slice(0, 4))
          : 2026,
      },
    })),
}));
