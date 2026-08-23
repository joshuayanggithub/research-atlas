# Compare: two authors or two institutions, side by side

Spec for review. Nothing here is built yet.

## The question this answers

"How do these two relate?" — and in practice that almost always means **where do they
overlap**: shared papers, shared topics, whether one's recent work has drifted into the other's
territory.

That is why this is **not split screen**. Two synchronised maps put A on the left and B on the
right, which makes the one thing you are looking for — the intersection — the hardest thing to
see, because it appears twice and matches nowhere. It also halves the map, which is worst on
mobile where the map is already the whole screen.

**Proposal: one map, two colours, third colour for the overlap, plus a comparison panel.**

## What it looks like

- The map keeps its full area. Papers belonging to A are one colour, B another, and papers
  belonging to **both** a third that reads as "special" rather than as a blend of two dots.
- Everything not in A ∪ B is hidden, exactly as an ordinary org/author filter already hides
  non-matching papers — so the comparison is the whole view, not a needle in the corpus.
- A panel gives the numbers the map cannot: counts, the shared papers themselves, and topic and
  year profiles for each side.

Colour must be colour-blind safe, so **not** red/green. Blue for A, amber for B, white for
both. Amber is already the app's "citer" colour and teal its "reference" colour, so the compare
palette deliberately avoids teal to keep those meanings intact; and because compare colouring
and a selected paper's citation colouring can be on screen together, that separation matters.

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
+ relevance, and zoom LOD — and deck.gl caps `filterSize` at 4. So compare must not ask for a
fifth.

It does not need one. Membership in A/B/both is a **colour** question, not a visibility one:

- **Filter channel 1** (`match`) becomes `mask !== 0` — the existing "hide non-matching"
  behaviour, unchanged.
- **Colour** gains a `ColorMode` of `"compare"`, alongside `subfield | org | recency`. The
  `rgb` memo in `usePointsLayer` already recomputes per colour mode; it gains the compare mask
  as a dependency.

Selection keeps working while comparing, and is better for it: click a shared paper and its
citation network still lights up, with each neighbour still carrying its A/B/both colour.

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
- Filling both slots switches colour mode to `compare` and hides everything else. Clearing
  either slot returns to the previous mode.
- **Mobile**: identical. The overlay needs no extra width, and the panel stacks under the map
  the way the author panel already does. This is the main reason to prefer overlay over split.

## Phasing

| phase | scope | why this order |
|---|---|---|
| 1 | mask, compare colour mode, counts, shared-paper list | The whole value proposition, and no pipeline work |
| 2 | topic + year profiles | Needs the placed-set handling above done properly |
| 3 | shared collaborators | Needs an `s10` precompute; do not attempt client-side |

## Risks

- **Colour legibility.** Three categories over a dark background at a million points, where
  dot size already encodes citations. Needs checking at the home view *and* zoomed in, on both
  viewports, before phase 1 is called done.
- **Two large orgs.** CMU (15,464) vs MIT (10,366) is a bigger on-screen set than any current
  filter produces. The mask itself is a linear pass, but the visual result may be a wash; worth
  measuring before assuming the design holds at that size.
- **Mode collision.** `colorMode` is user-controlled; compare overrides it. The override has to
  be visible and reversible, or it reads as the colour picker breaking.
