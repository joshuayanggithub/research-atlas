#!/usr/bin/env bash
# All-years build: 912,429 papers. Embeddings are already merged, so s03 is skipped.
# Edges come from project_edges.py (global S2 graph) instead of s09, which can only see
# per-row referenced_works and would miss most citations for post-2021 arXiv-only papers.
cd /home/joshua/Documents/research-atlas || exit 1
LOG=data/s2ag/merged_build.log
say(){ echo "[$(date '+%F %T')] $*" | tee -a "$LOG"; }

say "=== s04 project + s12 tiles + s05 cluster (912k) ==="
uv run python -m pipeline.run_all --only s04,s12,s05 >> "$LOG" 2>&1 || { say "FAILED at s04/s12/s05"; exit 1; }

say "=== phase 4: project global citation graph onto merged corpus ==="
uv run python project_edges.py >> "$LOG" 2>&1 || { say "FAILED at project_edges"; exit 1; }

say "=== s08 neighbors ==="
uv run python -m pipeline.run_all --only s08 >> "$LOG" 2>&1 || { say "FAILED at s08"; exit 1; }

say "=== s06 hierarchy + s07 labels (the expensive pair) ==="
uv run python -m pipeline.run_all --only s06,s07 >> "$LOG" 2>&1 || { say "FAILED at s06/s07"; exit 1; }

say "=== s14 rosters + s10 indexes + s11 emit ==="
uv run python -m pipeline.run_all --only s14,s10,s11 >> "$LOG" 2>&1 || { say "FAILED at s14/s10/s11"; exit 1; }

say "=== MERGED BUILD COMPLETE ==="
