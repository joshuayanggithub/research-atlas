#!/usr/bin/env bash
# Rebuild the map for the 1,000,490-paper corpus (1991-2026) after merge_backfill.py.
# Embeddings are already merged and unit-normalised, so s03 is skipped.
cd /home/joshua/Documents/research-atlas || exit 1
LOG=data/s2ag/rebuild_1991.log
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

say "=== s04 project + s12 tiles + s05 cluster (1,000,490) ==="
uv run python -m pipeline.run_all --only s04,s12,s05 >> "$LOG" 2>&1 || { say "FAILED at s04/s12/s05"; exit 1; }

say "=== phase 4: project global citation graph ==="
uv run python project_edges.py >> "$LOG" 2>&1 || { say "FAILED at project_edges"; exit 1; }

say "=== reference availability flag ==="
uv run python build_reference_availability.py >> "$LOG" 2>&1 || say "WARN: reference availability failed (s11 assumes available)"

say "=== s08 neighbors ==="
uv run python -m pipeline.run_all --only s08 >> "$LOG" 2>&1 || { say "FAILED at s08"; exit 1; }

say "=== s06 hierarchy + s07 labels (the expensive pair) ==="
uv run python -m pipeline.run_all --only s06,s07 >> "$LOG" 2>&1 || { say "FAILED at s06/s07"; exit 1; }

say "=== s14 rosters + s10 indexes + s11 emit ==="
uv run python -m pipeline.run_all --only s14,s10,s11 >> "$LOG" 2>&1 || { say "FAILED at s14/s10/s11"; exit 1; }

say "=== 1991 REBUILD COMPLETE ==="
