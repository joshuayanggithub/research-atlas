#!/usr/bin/env bash
# Publish the artifact bundle to the object store (Cloudflare R2).
#
# Two things this script exists to get right:
#
#  1. GZIP. Measured 2026-08-22 against the real bucket: R2 serves a plain .arrow upload as
#     application/octet-stream at full size even when the browser sends
#     "Accept-Encoding: gzip, deflate, br" — 2,346 bytes in, 2,346 bytes on the wire. The same
#     bytes uploaded pre-compressed with Content-Encoding: gzip go out at 82, and the edge
#     transparently inflates for clients that do not ask for gzip. Without this the whole
#     per-visit budget is wrong by ~2x. Measured ratio over the bundle: 0.475.
#
#  2. LOCAL-ONLY FILES. papers.arrow (263 MB) is a build-machine artifact nothing fetches
#     (D47). The exclusion is read from pipeline.common.schema.LOCAL_ONLY_FILES so the list
#     cannot drift from the code that defines it.
#
# Artifacts are published under an IMMUTABLE, versioned prefix so the edge can cache them
# forever and repeat visitors cost nothing:  v/<build-date>/…
#
# Usage:
#   tools/publish_artifacts.sh                 # dry run (default — prints what WOULD upload)
#   tools/publish_artifacts.sh --confirm       # actually upload
#   VERSION=2026-08-22 tools/publish_artifacts.sh --confirm
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="${SRC:-$REPO_ROOT/web/public/data}"
REMOTE="${REMOTE:-r2:research-atlas}"
VERSION="${VERSION:-$(date +%Y-%m-%d)}"
DEST="$REMOTE/v/$VERSION"
# The system rclone on this box is v1.53 (2020) and predates R2 entirely.
RCLONE="${RCLONE:-$HOME/.local/bin/rclone}"

CONFIRM=0
[[ "${1:-}" == "--confirm" ]] && CONFIRM=1

[[ -x "$RCLONE" ]] || { echo "rclone not found at $RCLONE (need >= 1.60 for R2)" >&2; exit 1; }
[[ -d "$SRC" ]] || { echo "no artifact directory at $SRC" >&2; exit 1; }

# Exclusions come from the pipeline, not from a copy pasted here.
mapfile -t LOCAL_ONLY < <(
  cd "$REPO_ROOT" && PYTHONPATH="$REPO_ROOT" .venv/bin/python -c \
    'from pipeline.common import schema; print("\n".join(sorted(schema.LOCAL_ONLY_FILES)))'
)
EXCLUDES=()
for f in "${LOCAL_ONLY[@]}"; do EXCLUDES+=(--exclude "$f"); done
echo "excluding (D47, build-machine only): ${LOCAL_ONLY[*]}"

# Pre-compress into a staging tree. rclone uploads these bytes verbatim and stamps the header,
# so what the browser receives is exactly what we compressed here.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
n=0; raw=0; gz=0
while IFS= read -r -d '' f; do
  rel="${f#"$SRC"/}"
  skip=0
  for x in "${LOCAL_ONLY[@]}"; do [[ "$rel" == "$x" ]] && skip=1; done
  [[ $skip == 1 ]] && continue
  mkdir -p "$STAGE/$(dirname "$rel")"
  gzip -6 -c "$f" > "$STAGE/$rel"
  raw=$(( raw + $(stat -c%s "$f") ))
  gz=$(( gz + $(stat -c%s "$STAGE/$rel") ))
  n=$(( n + 1 ))
done < <(find "$SRC" -type f -print0)

# bc does not parse 1e9.
printf 'staged %d files: %.2f GB raw -> %.2f GB gzipped (ratio %.3f)\n' \
  "$n" "$(bc -l <<< "$raw/1000000000")" "$(bc -l <<< "$gz/1000000000")" \
  "$(bc -l <<< "$gz/$raw")"
echo "destination: $DEST"

RCLONE_ARGS=(
  sync "$STAGE" "$DEST"
  --header-upload "Content-Encoding: gzip"
  # Immutable versioned prefix: these bytes never change under this path.
  --header-upload "Cache-Control: public, max-age=31536000, immutable"
  --transfers 8 --checkers 16 --stats-one-line --stats 10s
)

if [[ $CONFIRM == 0 ]]; then
  echo
  echo "DRY RUN — nothing will be uploaded. Re-run with --confirm to publish."
  "$RCLONE" "${RCLONE_ARGS[@]}" --dry-run 2>&1 | tail -5
  exit 0
fi

"$RCLONE" "${RCLONE_ARGS[@]}"
echo
echo "published. point the frontend at it with:"
echo "  VITE_DATA_BASE=https://pub-e9b4142dba374b438774d2bab6b4e09f.r2.dev/v/$VERSION"
