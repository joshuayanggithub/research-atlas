# Citation Graph Rebuild — plan and decisions

Decided 2026-08-16. Supersedes the corpus-scoped S2 citation approach in
`s16_enrich_s2_citations`. Companion to `DESIGN_DECISIONS.md` (rationale) and `TODO.md`
(task tracking).

**Goal:** build a permanent, corpus-independent local copy of the Semantic Scholar citation
graph, so that expanding Research Atlas to any year range, any arXiv category, or beyond
arXiv never requires re-downloading or re-scanning anything.

---

## 1. Why we are doing this at all

### OpenAlex cannot supply references for arXiv preprints

Measured on our own corpora (not vendor claims):

| cohort | n | mean OpenAlex refs | mean S2 refs | OA/S2 |
|---|---|---|---|---|
| 2022-2024 arXiv-only | 500 | **0.00** | 46.40 | **0.00** |
| 2015-2021 arXiv-only | 499 | 19.00 | 37.38 | 0.51 |
| 2015-2021 published elsewhere | 497 | 41.84 | 45.93 | 0.91 |
| 2022-2024 published elsewhere | 499 | 40.41 | 52.93 | 0.74 |

Two independent causes, both verified:

1. **References come from publisher (Crossref) deposits.** On the 2025-2026 corpus, papers
   whose only DOI is the arXiv-minted `10.48550/arxiv.*` have **0.0%** reference coverage
   (mean 0.01 refs, n=211,660). Papers with a real publisher DOI have 41.1% (mean 14.90).
   An arXiv-only preprint has no publisher, so it has no deposited bibliography — ever.
2. **The Microsoft Academic Graph shutdown (2021-12-31).** MAG did PDF-based reference
   extraction and OpenAlex inherited its records. arXiv-only papers *with* a legacy MAG id
   have references 88-95% of the time **in every year including 2024**; without one they have
   essentially none. MAG id coverage collapses 67.4% (2021) → 1.9% (2022) → 0.6% (2024).

**Consequence:** ~76% of our corpus is arXiv-only, and the boundary is fixed in time while
the corpus grows. Every future year is post-MAG. S2 is not a stopgap for recent papers; it is
permanently required for everything from 2022 onward.

For the record, in the 2015-2024 build OpenAlex still produced 1,039,027 edges — but
**827,671 (79.7%) come from 2015-2021**, i.e. from MAG-era records. 2022-2024 contributes
211,356 edges from 48% of the papers.

### The API cannot supply edges at scale

`/paper/batch` returns per-paper *fields* (500 ids/request — verified: 501 → HTTP 400). That
is fine for counts: 271,366 papers took **22 minutes**. But edges are only exposed per-paper
via `/paper/{id}/citations` and `/references`, so:

| corpus | API requests (floor) | API time | bulk S2AG |
|---|---|---|---|
| 271k | ≥542,732 | ≥151 h | **19.1 h** |
| 641k | ≥1,282,126 | ≥356 h | **19.1 h** |
| all arXiv ~2.7M | ≥5,400,000 | ≥1,500 h | **19.1 h** |

### The current design throws away 97% of each scan

`_scan_citations` applies the corpus filter *inside* the scan and `unlink()`s each shard:

```python
if source_i is None or target_i is None:
    continue     # external endpoint -> edge discarded permanently
```

Measured on the completed run: **20,269,136** edges touched a corpus paper, **597,120** were
retained, **~19.7M discarded** — a **2.9%** retention rate, unrecoverable without a full
re-download. That is why every scope change currently costs another 19 hours.

---

## 2. Decisions

| # | Decision | Rationale |
|---|---|---|
| D1 | **Scan globally, project locally.** The scan takes no corpus. | Cost is ~97% fixed (see §3). Corpus only sizes a hash table: 271k → 2.7M costs +0.5 h. |
| D2 | **Key everything on S2 `corpusid`**, not our `node_id`. | `node_id` is rebuild-transient; `corpusid` is stable and global. |
| D3 | **Store adjacency in BOTH directions** (`refs` and `cited_by`). | Deriving the reverse needs a full re-sort of 5.7B rows. Storing twice costs ~2x of a small number. |
| D4 | **Keep `citationid` and `isinfluential`** per edge, not just the endpoint pair. | `isinfluential` is real signal for edge weighting / the relevance slider, and costs ~1 bit. Dropping it was an error in the first draft of this plan. |
| D5 | **Archive the raw 422 GB on the WD drive.** | Extraction is *not* lossless (`contexts`/`intents` dropped). Re-download is 11 h; keeping it is free at 45% of an otherwise-unused 931 GB disk. |
| D6 | **Build the crosswalk from the `papers` dataset, not the API.** | `papers.externalids.ArXiv` → `corpusid` directly. 60 bulk shards, **zero API calls**, vs ~5,400 requests (~3.6 h). Also handles old-style ids (`math/0607666`). |
| D7 | **Download only `citations` + `papers`.** | `abstracts` is redundant (arXiv gives us 100% coverage and is authoritative for the embedding input); `publication-venues` is marginal; `s2orc` is not needed. |
| D8 | **Do NOT download `embeddings-specter_v2`.** Keep local GPU. | 986 shards ≈ **1,059 GB ≈ 27.5 h** to extract the 1.2% that are arXiv, vs **1.6 h** on the RTX 3090 for all 2.7M papers. Local GPU is ~17x faster. |
| D9 | **Use `orjson` + parallel shard downloads + pipelining.** | Measured: orjson is **3.0x** faster than stdlib (1.54M vs 510k rec/s). Download is 58% of wall time, so concurrency is the biggest lever. |

### Explicitly rejected

- **Filtering to arXiv-only during extraction** (~360 MB instead of ~40 GB). Forecloses
  non-arXiv work permanently to save 0.004% of a free disk. Rejected.
- **Storing only the flat edge list.** Same data, but "references of paper X" becomes a scan
  of 45 GB instead of an O(1) slice. Rejected in favour of adjacency (D3).
- **Merging the corpora first.** An earlier draft said to rescan "against a merged 2015-2026
  corpus" — that contradicts D1 and is wrong. There is no corpus in the scan.

---

## 3. Where the 19 hours goes

Measured, not estimated: one citations shard is **1,074 MB**, throughput **10.7 MB/s**.

| component | time | scales with corpus? |
|---|---|---|
| Download 422 GB @ 10.7 MB/s | **11.0 h** | no |
| JSON parse 5.7B records (stdlib) | 3.1 h | no |
| 11.4B hash lookups | 0.5 h (271k) → 1.0 h (2.7M) | slightly |
| gzip + per-row Python | ~4.5 h | no |
| **total observed** | **19.1 h** | |

Download dominates. With D9 the realistic target is **5-8 h**, but the plan below budgets the
unoptimised path so a slow night is not a failure.

---

## 4. Artifacts produced

Written under `data/s2ag/` (fast NVMe), archive on the WD.

| artifact | contents | est. size |
|---|---|---|
| `papers.parquet` | `corpusid → arxiv_id, title, year, venue, referencecount, citationcount` | ~10-15 GB |
| `refs.parquet` | `corpusid → [cited corpusids]`, sorted, + `isinfluential` | ~12-20 GB |
| `cited_by.parquet` | `corpusid → [citing corpusids]`, sorted | ~12-20 GB |
| `crosswalk.parquet` | `arxiv_id ↔ corpusid` (incl. old-style ids) | < 1 GB |
| **raw archive (WD)** | untouched `citations` + `papers` shards | **422 GB + ~60 GB** |

Total on NVMe ~40-55 GB against 378 GB free.

---

## 5. Phases and time estimates

| phase | what | est. time | blocking? |
|---|---|---|---|
| **0** | Mount the WD drive (`sda1`, NTFS, 931 GB) read-write | 2 min | **needs sudo — user action**; verified 2026-08-16 to be empty (129 MB of Windows system folders, 932 GB free) |
| **0b** | ~~Move the 20 GB historical build out of `/tmp`~~ | — | **DONE 2026-08-16** → `~/research-atlas-historical/` (same-filesystem rename; venv and 339-file bundle verified intact) |
| **1** | Download + parse `papers` (60 shards) → `crosswalk.parquet` + `papers.parquet` | **1.5-2 h** | no |
| **2** | Stream `citations` (393 shards) → global edge list, archiving raw to WD | **11-19 h** | needs phase 0 for the archive |
| **3** | Sort/group into `refs.parquet` + `cited_by.parquet` | **1-2 h** | |
| **4** | Projection stage: corpus + crosswalk + adjacency → `edges.arrow` | **seconds**, per corpus | |
| **5** | Rebuild bundle (`s09,s08,s10,s11`) and browser-verify | ~30 min | |
| | **total one-time** | **~15-24 h** | |

After phase 3, **every future corpus change is phase 4 + 5 only (~30 min)** — no download, no
rescan, regardless of years, categories, or corpus size.

---

## 6. Prerequisites / open items

1. **Mount the WD** (phase 0) — required only for the raw archive (D5). Phases 1 and 3-5 can
   proceed without it; phase 2 can too if we skip archiving. The drive was inspected read-only
   on 2026-08-16: effectively empty (129 MB, only `$RECYCLE.BIN` + `System Volume Information`).
   **Open sub-decision:** keep NTFS (Windows-readable, but `ntfs-3g` is FUSE/userspace and may
   write slower than the 10.7 MB/s download, making the archive the bottleneck) vs reformat to
   ext4 (fast, no Windows-hibernation corruption risk, not Windows-readable). Benchmark the
   write speed once mounted rw before committing 422 GB to it.
2. **`contexts` / `intents` sampling** — measure how often they are populated before deciding
   whether the extracted form should carry them. They were `null` in every sampled record but
   are the plausible bulk of the 422 GB.
3. **S2 affiliation quality is unmeasured.** `papers.authors` and the `authors` dataset carry
   affiliations. Given org attribution currently reaches only **12.1%** of the corpus
   (32,907/271,366) this is worth testing — but no claim is made here until measured.
   Note attribution *precision* is already good (98-100% for 11 of 12 curated orgs); the
   failure is recall.
4. **Nothing in this plan is committed yet.** The arXiv-backbone switch itself
   (`s01_fetch_arxiv.py`, `s02_build_arxiv_corpus.py`, `s15`, `s16`) is still untracked.

---

## 7. Run log — 2026-08-16

### Phase 1 (crosswalk) — PARTIAL, then fixed

Scanned all 60 `papers` shards in 76.9 min at ~51.8k rec/s: **237,167,341 papers**, of which
**3,097,789 carry an arXiv id** (3,097,524 unique). `crosswalk.parquet` written successfully.

`papers.parquet` **failed — OOM-killed** (no traceback; RAM dropped 20 GB → 3 GB). Cause was a
design flaw in `build_s2_crosswalk.py`: it accumulated all 237M rows in six Python lists before
writing (~34 GB for the ints alone, plus tens of GB of title/venue strings, on a 78 GB box).
The crosswalk survived only because it is 3.1M rows.

**Fixed:** papers are now flushed to `data/s2ag/papers_shards/papers_NNNN.parquet` per shard and
merged with a lazy `scan_parquet().sink_parquet()`, so nothing materialises at full size.
**Not re-run tonight** — it would compete for bandwidth with the phase 2 critical path, and
`papers.parquet` is a nice-to-have (titles, authoritative counts). `crosswalk.parquet` is the
only phase 1 output phases 2-4 depend on, and it is complete.

### Incident: driver deadlock caused by the monitoring loop

The driver waits with `while pgrep -f build_s2_crosswalk`. Monitoring shells whose *command
lines contained that literal string* matched the pattern, so the wait loop kept seeing a
"running" phase 1 after it had died — including a background watcher that matched itself and
so could never exit either. Two processes deadlocked via pgrep self-matching.

**Resolved** by killing the stale watchers; the driver advanced within 40 s.
**Rule going forward:** all monitoring must use bracket-escaped patterns (`[b]uild_s2_...`),
which match the target process but not the monitoring command's own command line.

### Phase 2 (edges) — RUNNING from 03:16

Started in **scratch mode**: `/mnt/wd` mounts rw and lists fine but returns ENOENT on every
create for a non-root user (mounted by root without uid/gid mapping), so the raw archive (D5)
is skipped. Every edge is still extracted — D1-D4 unaffected. To enable archiving later:

```bash
sudo mount -o remount,rw,uid=$(id -u),gid=$(id -g) /mnt/wd
```

393 shards to process. Manifest fetch hit HTTP 429 twice and recovered via built-in backoff.

### Phase 3 rewritten pre-emptively (bucketed external group-by)

The first `build_s2_adjacency.py` used a single lazy
`scan_parquet().group_by(key).agg(...).sort(key).sink_parquet()`. Measured on 8 of 393 edge
shards that peaked at **9.6 GB RSS for a 473 MB output** (~20x overhead) — extrapolating to
~4.9B edges puts it in the hundreds of GB, i.e. the same OOM that killed the phase 1 papers
table. Caught by testing before phase 2 finished, not by a 3am crash.

Replaced with a classic external group-by: hash each key into 64 buckets (one source shard in
memory at a time), group each bucket independently, then concatenate — buckets are disjoint by
construction so no merge logic is needed. Verified on a 6-shard subset:

| | naive | bucketed |
|---|---|---|
| peak RSS | 9.6 GB | **1.3 GB** |
| distinct keys | — | 39,271,266 (exact match) |
| edges preserved | — | 70,213,602 (exact match) |

Peak memory is now bounded by bucket size rather than total edge count.

### Phase 2 COMPLETE — 2026-08-16 12:02 (8.75 h)

**5,089,547,933 edges** extracted from all 393 shards, keyed on `corpusid`, with `citationid`
and `isinfluential` retained (D4). One transient error in the whole run (shard 137, presigned
URL expiry at 06:27) which self-recovered via the manifest-refresh retry.

### FINDING: `contexts` / `intents` are 35% populated — D5 was right and we lost it

The open question in §6.2 was whether the dropped `contexts`/`intents` fields justify the
422 GB raw archive. Sampled over 2,000,000 records during the scan:

| field | populated |
|---|---|
| `contexts` | **35.03%** (700,655 / 2,000,000) |
| `intents`  | **35.03%** (700,655 / 2,000,000) |

Earlier hand-samples showed `null` for both, which led me to call the archive "defensible to
skip". That was wrong — those early records were unrepresentative. At scale roughly **1.78
billion** citation records carry the surrounding sentence and an intent label
(background / method / comparison). That is substantial signal we did **not** keep, because the
WD archive was unwritable (ntfs-3g uid mapping) and phase 2 fell back to scratch mode.

**Consequence:** recovering citation contexts later means re-downloading the 422 GB citations
dataset (~11 h at the measured ~9-10 MB/s link ceiling). The edge graph itself is complete and
unaffected — this is purely the loss of context/intent metadata.

**Recommendation:** if citation contexts are wanted (e.g. to explain *why* one paper cites
another, or to weight edges by intent), fix the mount and re-run phase 2 with archiving
enabled, rather than discovering the need later:

```bash
sudo mount -o remount,rw,uid=$(id -u),gid=$(id -g) /mnt/wd
```

### Phase 3 in progress — bucketed rewrite confirmed at full scale

`refs.parquet`: **97,136,551 keys, 17.0 GB, 17.8 min**, peak RAM ~4 GB on a 78 GB box.
The naive `group_by` this replaced projected into the hundreds of GB.

### PHASE 3 COMPLETE — 2026-08-16 12:40. Pipeline done in 10.9 h total.

| artifact | contents | size |
|---|---|---|
| `data/s2ag/crosswalk.parquet` | 3,097,789 `arxiv_id ↔ corpusid` (incl. old-style `math/0607666`) | 0.03 GB |
| `data/s2ag/refs.parquet` | 97,136,551 keys → papers each one cites | 17.0 GB |
| `data/s2ag/cited_by.parquet` | 109,580,699 keys → papers citing each one | 17.7 GB |

**5,089,547,933 edges**, verified in both directions against parquet metadata.

### Validation gotcha: polars aggregations overflow at 2^32

`pl.scan_parquet(glob).select(pl.len())` returned **794,580,637** — exactly
`5,089,547,933 − 2^32`. The same wrap hit `pl.col("neighbors").list.len().sum()`. The data was
never wrong; the *counter* was. Casting to `Int64` before summing gives the correct total:

```python
pl.col("neighbors").list.len().cast(pl.Int64).sum()   # correct
pl.col("neighbors").list.len().sum()                  # wraps above 2^32
```

Always cast before aggregating over this graph — every total here exceeds 2^32.

### Validation: adjacency is faithful to the raw edges

For arXiv 2512.24601 (corpusid 284350669), adjacency and a direct scan of the edge shards agree
exactly: 69 outgoing rows, 104 incoming rows. Attention Is All You Need: **356,593 citations**.

**The bulk data contains duplicate `(src,dst)` pairs** — those 69 outgoing rows resolve to only
**35 distinct** cited papers (~2x duplication). This is the mechanism behind the ~1.68x count
inflation measured earlier. S2's own `referenceCount=60` counts *parsed* references including
targets with no `corpusid`, which can never appear in an edge file — so 35 in-corpus distinct
targets out of 60 parsed references is consistent, not a defect.

**Implication for phase 4 (projection):** deduplicate `(src,dst)` when building `edges.arrow`,
or the browser graph will draw duplicate arrows.

### Remaining

- **Phase 4** (project corpus + crosswalk + adjacency → `edges.arrow`) and **phase 5** (rebuild
  bundle, browser-verify) are not yet written. A filtered lookup over the 17 GB adjacency took
  ~54 s unindexed; phase 4 should join on a sorted/filtered corpusid set rather than scanning
  per paper.
- `papers.parquet` still missing (phase 1 OOM; script fixed, needs a ~1.6 h re-run).
- Citation `contexts`/`intents` (35% populated) were not archived — see the D5 finding above.

### Phase 4 COMPLETE — 2026-08-16 13:55 (18 seconds)

`project_edges.py` replaces `s09_edges` for arXiv-spine builds: it projects the global 5.09B-edge
graph onto the active corpus via the crosswalk, instead of relying on per-row
`referenced_works` (which OpenAlex cannot supply for post-2021 arXiv-only preprints).

| | edges |
|---|---|
| previous `s16` corpus-scoped 19 h scan | 597,120 |
| phase 4 projection off the global graph | **597,142** |

Agreement to **0.004%** — two independent paths through the same S2 release converging is strong
validation of both. 267,315/271,366 (98.5%) of corpus papers resolved to a `corpusid`.
Output verified: node_ids in range, **0 self-loops, 0 duplicate pairs** (dedupe per §7 finding).

**The current bundle therefore barely changes** — `s16` already used S2 for this corpus. The
payoff is prospective: projecting the 2015-2024 corpus, a merged 2015-2026 corpus, or any
future topic/date scope now costs ~18 s instead of ~19 h.

**Runtime note:** filter-then-explode is what makes this fast. Filtering `refs.parquet` by
`src ∈ corpus` prunes 97M keys to ~267k *before* exploding; exploding first would materialise
5.09B rows. (A naive per-paper filtered scan of the 17 GB adjacency measured ~54 s for a
*single* paper.)

### Phase 5 COMPLETE + browser-verified — 2026-08-16 14:30

`s08,s10,s11` rebuilt from the phase 4 edges. `edges.arrow` = 597,142 rows (4.78 MB).
Verified in Chromium at desktop (1440x900) and mobile (iPhone 13) per AGENTS.md:

- canvas renders non-background pixels at both viewports
- citation counts show provenance: `439 citations · Semantic Scholar S2AG (2026-08-11) + OpenAlex fallback`
- citation network renders: References 164 / Cited by 361 / Both 525, reference rows populate
- **zero console/page errors at both viewports**

### DECIDED: keep the raw archive, do NOT extract contexts to parquet

Measured on 400,000 real citation records during the archive run:

| | |
|---|---|
| records with contexts | 161,575 (**40.4%**) |
| context sentences per such record | 2.15 |
| avg bytes per record *with* contexts | 468 B |
| avg bytes across *all* records | 189 B |

Extrapolated to all 5,089,547,933 edges: **~961 GB uncompressed, ~320 GB at zstd 3x** — against
a **422 GB** gzipped raw archive that contains contexts *and* every other field.

**Verdict: extraction is not worth it.** A contexts-only parquet would cost ~76% of the full
raw archive while discarding everything else. Keep the raw shards on `/mnt/wd` and extract
contexts on demand for a chosen subset (e.g. only edges between corpus papers), which is a
local operation over archived data.

This supersedes the D5 uncertainty: the archive is the correct storage format, and the earlier
advice to skip it was wrong on both counts (contexts are 40% populated, not absent; and the
archive is the *cheaper* way to hold them).

*(Measurement caveat: `intent_bytes` sampled as 0 because the sampler only summed `str` items
and `intents` entries are not plain strings. Intents are present on the same 40.4% of records;
their size is small relative to context text and does not change the verdict.)*

### MEASURED AND REJECTED: S2 as an organization-data source (2026-08-16)

Plan §6.3 flagged S2 affiliations as "unmeasured, worth testing". Tested — **S2 is far worse
than OpenAlex for org data, and the idea is dropped.**

Two things I had assumed were wrong:

1. **`papers.authors` does not carry affiliations.** Sampled 569 paper records: the author
   entries contain exactly `authorId` and `name`. Nothing else.
2. **The `authors` dataset's affiliations are essentially empty.** Two independent shards:

| | shard 0 | shard 15 |
|---|---|---|
| authors sampled | 7,628 | 7,638 |
| `affiliations` populated | **0.18%** | **0.17%** |
| prolific authors (>=20 papers) with affiliation | 0.2% | 0.6% |
| `papercount` populated (sanity check) | 98.7% | 98.4% |

The `papercount` check confirms the shards are real records, not a degenerate sample — the
affiliations are simply absent.

**OpenAlex gives 12.1% of papers institution evidence; S2 gives ~0.2% of authors an
affiliation string — roughly 60x worse.** Switching would have degraded org attribution.

The 12.1% org recall gap is still the biggest weakness in the org feature, but S2 is not the
fix. The viable paths remain those already in `ROADMAP.md` §P0.1: benchmark against **AffRo**
(F1 0.937 vs OpenAlex's 0.921 on CC0 AffRoDB), and scale the curated author-roster mechanism
(`org_rosters.yaml`), which is currently a one-org pilot.

### All-years merge — 2026-08-16 15:00

`merge_corpora.py` combines the two builds into one corpus:

| | papers |
|---|---|
| 2025-2026 (live) | 271,366 |
| 2015-2024 (historical) | 641,063 |
| **merged** | **912,429** (2015..2026, zero arXiv-id overlap) |

**No re-embedding.** Both builds used `specter2_local` /
`allenai/specter2_base+allenai/specter2:proximity` at 768-d with 100% coverage, so the vectors
concatenate directly (verified: 912,429x768, all unit-norm, finite). Re-embedding would have
been the single most expensive step; this made the merge a 0.2 min operation.

**Bug caught during the merge — type narrowing destroyed data.** The first version aligned the
historical frame to the live schema unconditionally. `subfield_name` is an all-`Null` column in
the live corpus but a populated `String` column in the historical one, so the cast silently
discarded **640,152 values** — and `s10_indexes` builds the topic-filter names from that
column. Fixed to always widen (adopt the non-`Null` type); re-ran from backup and confirmed all
640,152 values retained.

Build order (`run_merged_build.sh`, tmux `merged-build`): `s04,s12,s05` -> `project_edges.py`
-> `s08` -> `s06,s07` -> `s14,s10,s11`. **s03 is skipped** (vectors pre-merged) and **s09 is
replaced by phase 4**, since s09 can only resolve per-row `referenced_works` and would miss most
citations for post-2021 arXiv-only papers.

Rollback: `data/interim/_pre_merge_backup/` holds the pre-merge corpus/embeddings;
`/tmp/bundle_2025_2026_backup` holds the emitted 2025-2026 bundle.
