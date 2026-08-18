#!/usr/bin/env bash
# Unattended driver for the citation-graph rebuild (docs/CITATION_GRAPH_PLAN.md).
# Phase 1 may already be running; wait for it, then run phases 2 and 3 back to back.
# Every phase is resumable, so re-running this script after any interruption is safe.
cd /home/joshua/Documents/research-atlas || exit 1
export S2_KEY=$(grep "^S2_API_KEY=" .env | cut -d= -f2)
LOG=data/s2ag/pipeline.log
mkdir -p data/s2ag
say() { echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

say "=== waiting for phase 1 (crosswalk) to finish ==="
while pgrep -f build_s2_crosswalk >/dev/null 2>&1; do sleep 60; done
if [ ! -f data/s2ag/crosswalk.parquet ]; then
  say "phase 1 produced no crosswalk — retrying once"
  uv run python build_s2_crosswalk.py >> data/s2ag/phase1_crosswalk.log 2>&1
fi
say "phase 1 done"

# Phase 2 is the long pole (~12h, link-limited at ~9-10 MB/s). Retry the whole phase a few
# times: it is checkpointed per shard, so a retry resumes rather than restarts.
for attempt in 1 2 3; do
  say "=== phase 2 attempt $attempt (citations -> edges) ==="
  if uv run python build_s2_edges.py >> data/s2ag/phase2_edges.log 2>&1; then
    say "phase 2 done"; break
  fi
  say "phase 2 attempt $attempt exited nonzero; resuming in 120s"
  sleep 120
done

say "=== phase 3 (adjacency) ==="
if uv run python build_s2_adjacency.py >> data/s2ag/phase3_adjacency.log 2>&1; then
  say "phase 3 done"
else
  say "phase 3 FAILED — see data/s2ag/phase3_adjacency.log"; exit 1
fi

say "=== ALL PHASES COMPLETE ==="
ls -la data/s2ag/*.parquet 2>/dev/null | awk '{printf "  %-46s %.2f GB\n", $9, $5/1e9}' | tee -a "$LOG"
