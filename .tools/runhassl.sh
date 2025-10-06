#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------
# Put your .hassl files here (absolute or relative paths).
# Example:
# MY_HASSL=( "rooms/den.hassl" "rooms/dining.hassl" "wesley_room.hassl" )
MY_HASSL=( )

# Where to put generated Home Assistant packages.
# Default is a local "build_hassl" folder so we don't nuke random dirs.
OUT_ROOT="./build_hassl"

# Optional: pass extra args to hasslc (leave empty for none)
# HASSLC_ARGS="--verbose"
HASSLC_ARGS=""

# ------------------------------------------------------------
# SAFETY CHECKS
# ------------------------------------------------------------
if ! command -v hasslc >/dev/null 2>&1; then
  echo "error: hasslc not found in PATH" >&2
  exit 1
fi

if [ ${#MY_HASSL[@]} -eq 0 ]; then
  echo "error: MY_HASSL is empty. Edit this script and add your .hassl files." >&2
  exit 1
fi

# ------------------------------------------------------------
# CLEAN OUTPUT TREE (ONLY OUT_ROOT)
# ------------------------------------------------------------
mkdir -p "$OUT_ROOT"

# Remove all subdirectories inside OUT_ROOT (but not OUT_ROOT itself)
shopt -s nullglob
for d in "$OUT_ROOT"/*/ ; do
  echo "rm -rf $d"
  rm -rf "$d"
done
shopt -u nullglob

# ------------------------------------------------------------
# BUILD
# ------------------------------------------------------------
failed=()

for f in "${MY_HASSL[@]}"; do
  if [ ! -f "$f" ]; then
    echo "warn: skipping '$f' (not found)" >&2
    failed+=("$f (missing)")
    continue
  fi

  name="$(basename "$f" .hassl)"
  outdir="$OUT_ROOT/$name"

  echo "==> Building: $f -> $outdir"
  mkdir -p "$outdir"

  if ! hasslc $HASSLC_ARGS "$f" -o "$outdir"; then
    echo "error: hasslc failed for $f" >&2
    failed+=("$f")
    continue
  fi
done

# ------------------------------------------------------------
# SUMMARY
# ------------------------------------------------------------
if [ ${#failed[@]} -gt 0 ]; then
  echo
  echo "Build completed with errors in:"
  for x in "${failed[@]}"; do echo "  - $x"; done
  exit 1
fi

echo
echo "All HASSL integrations rebuilt in: $OUT_ROOT"
