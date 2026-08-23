# Compare: two authors or two institutions, side by side

Spec for review. Nothing here is built yet.

## Reconsidered: split screen, not overlay

**The first version of this spec argued for a single map with A, B and both in three colours.
That was wrong, and the reasoning is worth keeping because it is the kind of mistake this
project keeps catching.**

Three things were underweighted:

1. **Occlusion makes an overlay lie.** Carnegie Mellon (15,464) against MIT (10,366) is ~25,000
   dots in three colours on one surface. Whichever draws last wins each pixel, so the apparent
   ratio of A to B is an artifact of **draw order, not data**. That is precisely the failure
   mode D49, D51 and D56 exist to prevent — a visual that reads as fact and is not one. It was
   filed as "a risk to measure" when it is a design objection.
2. **The intersection is usually tiny.** Two authors' co-authored papers are frequently 0-5, and
   jointly-affiliated institutional papers are a thin slice. The whole overlay argument rested
   on "the question is overlap" — but when the third colour is nearly empty, what is actually
   being compared is **shape**: two distributions over the same space. Small multiples is the
   canonical answer to that, and split screen is small multiples.
3. **The cost objection to split was factually wrong.** It assumed two maps meant two WebGL
   contexts and therefore two picking readbacks — the operation that profiling showed dominates
   (D60). deck.gl 9 supports multiple `views` on a **single** canvas with `layerFilter`: one
   context, one picking pass. Split costs far less than assumed.

The mobile objection ("halves the map") was also weak: panes can stack vertically, which is
still small multiples.

## The question this answers

"How do these two relate?" — which splits into two questions that want different answers:

| question | answered by |
|---|---|
| *Do they work on the same things?* (the common case) | **two maps, side by side** — shape against shape |
| *What exactly do they share?* | **a list**, not a colour — the panel's shared-paper section |

The intersection does not need a colour on the map. It needs to be enumerable, and a list does
that better than a hue that may apply to four dots.

## What it looks like

- **Two panes, one deck.gl canvas**, via `views: [OrthographicView('a'), OrthographicView('b')]`
  plus `layerFilter` to route each side's layers to its own pane. Side by side on desktop,
  stacked on mobile.
- **Linked viewports.** Both panes share one `viewState`, so panning or zooming either moves
  both. This is what makes the comparison honest: the same region of semantic space is under
  the same screen position in both panes, so "A is dense here, B is empty here" is directly
  readable. Unlinked panes would make the two maps incomparable.
- **Shared papers highlighted in both panes.** The intersection appears in each side, in the
  same accent, so it is visible without needing a third category on a single crowded surface.
- Each pane is labelled with its side and paper count, permanently — an unlabelled pane in a
  screenshot is unattributable.

Colour: each pane needs only ONE colour for its own papers plus the shared accent, so the
palette pressure that pushed the overlay toward three simultaneous hues disappears. Panes can
keep the existing subfield colouring if that turns out to read better, since they no longer
have to encode side identity — position in the pane does that.

## Data model

A new store slice, kept out of `filters` so two filter systems never compete:

```ts
type CompareSide =
  | { kind: "author"; ids: number[]; label: string }   // an identity group (D59 same_name_ids)
  | { kind: "org";    keys: string[]; label: string }; // one org key, or a unit + children

interface CompareState { a: CompareSide | null; b: CompareSide | null; }
```

A side is a *set*, not a single id, because an author is routinely several rows (D59) and an org
may include its sub-units. Both cases already resolve to node sets today.

### The mask

`useCompareMask(ds, compare) -> Uint8Array` over all nodes: `0` none, `1` A, `2` B, `3` both.

It composes the same sources the ordinary filters already use, so there is **no new artifact and
no pipeline work**:

- authors → `useAuthorPapers` (author-papers shards, D30)
- curated orgs → inline `node_ids`
- directory orgs → `useOrgNodes` (org-node shards, D50)

## Why this fits the renderer

The GPU filter has **four channels and all four are taken** — date, org/author match, selection
+ relevance, and zoom LOD — and deck.gl caps `filterSize` at 4. Split screen does not ask for a
fifth: each pane renders its own points layer with its own `match` channel, and `layerFilter`
decides which pane draws which layer. Two layers, one context, one picking pass.

The mask still does the work: `useCompareMask` yields `0 | 1 | 2 | 3`, pane A shows
`mask & 1`, pane B shows `mask & 2`, and the shared accent is `mask === 3` in both.

**What must be measured before this is called done:** rendering two point layers means the
per-frame attribute work happens twice. The layers are the same 1M-element typed arrays with
different filter values, so the incremental cost should be small — but "should be" is not a
measurement, and D60 is a reminder that the expensive thing in this renderer was not where it
was assumed to be.

## The comparison panel

Ordered by how often it answers the actual question.

1. **Counts** — `|A|`, `|B|`, `|A ∩ B|`. The headline. For two authors the intersection is
   their co-authored papers; for two institutions, jointly-affiliated work.
2. **The shared papers themselves** — a list of `A ∩ B`, most-cited first. Usually short and the
   most concretely useful thing on the panel. Titles come from title shards (D55), so a capped
   list is cheap; cap at 50 and say so.
3. **Topic profile** — top fields/subfields for each side, with shared ones marked. Answers
   "do they even work on the same things?" when the paper intersection is empty.
4. **Year profile** — papers per year per side, as two small sparklines. Shows trajectory:
   who was active when, and whether they converged.
5. **Citation profile** — total and median citations per side, subject to the existing
   availability flag (D39): a paper with no provider-backed count must not be read as zero.

**Deferred: shared collaborators.** Per-paper `author_ids` left the resident index in D30 and
now live in per-paper detail shards, so computing the co-author sets of two prolific researchers
would mean hundreds of shard fetches. It is the one genuinely expensive item here. If it is
wanted later, the honest route is a precomputed per-author collaborator list in `s10`, the same
way `top_authors` and author affiliations already are.

## Honesty requirements

This is where comparisons go wrong, so they are requirements, not nice-to-haves.

- **Never report a count before both sides have resolved.** Org membership arrives per shard;
  reporting `0 shared papers` while a shard is in flight is the exact failure D51 fixed for the
  filter bar. The panel shows the same pending treatment until both sides are complete.
- **Never compute topic or year profiles from downloaded points alone.** `subfieldId` and `year`
  are zero for a point whose tile has not arrived, and tiles are importance-ordered — so a
  profile built from whatever has loaded is biased toward famous papers and *changes as you
  wait*. Same trap as D49/D51/D56. Either depend on the tiles epoch and show the profile as
  pending until the matched set is placed (`ensurePositionsFor` already fetches exactly those),
  or precompute.
- **Say what "shared" means.** For institutions, a shared paper means jointly affiliated *as far
  as our attribution goes* — and attribution is ~6% for 2026 work (the COMET gap) and misses
  companies without the curated matchers (D43). A comparison involving 2026 or a company will
  understate. The panel should carry that caveat where the number is, not in a footnote.
- **An empty intersection is a real answer**, and must read as "no shared papers" rather than as
  a broken panel.

## Interaction

- A **Compare** section in the sidebar with two slots. Each accepts an author or an
  organization through the existing search components — no new search UI.
- Filling both slots splits the map. Clearing either returns to the single view.
- **One viewState for both panes.** Zoom/pan is shared; there is no per-pane camera.
- **Selection while comparing**: clicking a paper in either pane selects it globally and its
  citation network lights up in both panes, which is informative — it shows whether the
  selected paper's influence reaches the other side.
- **Mobile**: panes stack vertically rather than side by side. Each gets roughly half the
  height, which is why per-pane labels and counts matter more here than on desktop.

## Phasing

| phase | scope | why this order |
|---|---|---|
| 1 | mask, compare colour mode, counts, shared-paper list | The whole value proposition, and no pipeline work |
| 2 | topic + year profiles | Needs the placed-set handling above done properly |
| 3 | shared collaborators | Needs an `s10` precompute; do not attempt client-side |

## Risks

- **Half the map each.** This is split screen's real cost and it is not free: at the home view
  a pane is ~720x900 on desktop and ~390x330 on mobile. Whether the semantic structure is still
  legible at that size is the first thing to check, and it is the one finding that would send
  this back to an overlay.
- **Two point layers.** Per-frame attribute work happens twice. Expected to be small since both
  layers share the same typed arrays, but measure it — D60 is the standing reminder that the
  expensive operation in this renderer was not the obvious one.
- **An empty pane reads as broken.** If one side has few papers at the current zoom, its pane
  looks like a failure rather than a finding. It needs an explicit "N papers, none at this zoom
  level" state.
- **Linked zoom can mislead in the other direction.** Sharing a viewport is what makes panes
  comparable, but if one side's work is concentrated somewhere the shared camera is not
  looking, it is invisible in both. The per-pane count (always visible) is what keeps that
  honest.
