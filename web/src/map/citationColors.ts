// One palette for "how does this paper relate to the selected one", shared by the edge layer
// and the points layer so a node and the edge touching it can never disagree.
//
// The direction names are deliberately about *influence*, not graph jargon, because that is
// what a reader actually wants off the map:
//
//   REFERENCE — the selected paper cites it. It came first and INFLUENCED the selection.
//   CITER     — it cites the selected paper. It came later and was INFLUENCED BY the selection.
//
// Encoding rule: hue carries direction, alpha carries strength, geometry stays constant. Size
// is already spoken for by citation count, so it must not also mean direction.

/** Selected paper cites this one — a reference; it influenced the selection. */
export const REFERENCE = [55, 214, 199] as const; // teal
/** This one cites the selected paper — a citer; it was influenced by the selection. */
export const CITER = [244, 162, 97] as const; // amber
/** Ambient, non-selection citation edges drawn across the whole map. */
export const GLOBAL_EDGE = [116, 151, 184] as const; // muted blue
/** The selected paper itself. */
export const SELECTED = [255, 255, 255] as const;

export type Rgb = readonly [number, number, number];

/** Direction colour for a link, given whether it points away from the selected paper. */
export function directionColor(outgoing: boolean): Rgb {
  return outgoing ? REFERENCE : CITER;
}
