#!/usr/bin/env bash
# Resume the all-years (912,429 paper) build from s08.
#
# History of this run:
#   15:22  s12 failed — TilingCfg lacked top_fraction in an already-imported config module.
#          Fixed; s04's coords2d.npy survived, so t-SNE was not repeated.
#   16:31  s12, s05 and phase 4 complete (13,006,390 edges).
#   18:20  s08's fuse step killed after ~2h. It was NOT hung: with 13M edges the candidate
#          loops cost sum(indeg^2)+sum(outdeg^2) = 30.9 BILLION inner steps (~21h projected),
#          49% of it from five hub papers with in-degrees 74k/69k/49k/34k/32k.
#          fused.hub_degree_limit=1000 now skips those pivots (14x less work, ~91 min) and
#          _jaccard no longer allocates a union set. Direct citations are still never capped.
#
# Everything s08 consumes (coords2d, cluster_assign, reveal_levels, edges.npz, embeddings,
# corpus_active) is already on disk, so this picks up at s08.
cd /home/joshua/Documents/research-atlas || exit 1
LOG=data/s2ag/merged_build.log
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

for f in coords2d.npy cluster_assign.npy reveal_levels.npy edges.npz; do
  [ -f "data/interim/$f" ] || { say "ABORT: data/interim/$f missing"; exit 1; }
done

say "=== RESUME @ s08 neighbors (hub_degree_limit=1000) ==="
uv run python -m pipeline.run_all --only s08 >> "$LOG" 2>&1 || { say "FAILED at s08"; exit 1; }

say "=== s06 hierarchy + s07 labels (the expensive pair) ==="
uv run python -m pipeline.run_all --only s06,s07 >> "$LOG" 2>&1 || { say "FAILED at s06/s07"; exit 1; }

say "=== s14 rosters + s10 indexes + s11 emit ==="
uv run python -m pipeline.run_all --only s14,s10,s11 >> "$LOG" 2>&1 || { say "FAILED at s14/s10/s11"; exit 1; }

say "=== MERGED BUILD COMPLETE ==="
