#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

target="${NORTH_STAR_SCROLLBACK_TARGET:-${1:-}}"

if [[ -z "$target" ]]; then
  profile_root="artifacts/qc_profile_p10_scrollback_ghostty_native"
  before_file="$(mktemp)"
  after_file="$(mktemp)"
  cleanup() {
    rm -f "$before_file" "$after_file"
  }
  trap cleanup EXIT

  find "$profile_root" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort >"$before_file" || true
  bash scripts/qc_profile_matrix.sh p10-scrollback-ghostty-native
  find "$profile_root" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort >"$after_file" || true

  target="$(comm -13 "$before_file" "$after_file" | tail -n 1)"
  if [[ -z "$target" ]]; then
    target="$(find "$profile_root" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -n 1 | cut -d' ' -f2-)"
  fi
fi

if [[ -z "$target" || ! -d "$target" ]]; then
  echo "north-star scrollback audit target was not produced or does not exist: ${target:-<empty>}" >&2
  exit 1
fi

echo "[qc:gate:north-star-scrollback] auditing $target"
node --import tsx tools/assertions/northStarScrollbackAudit.ts "$target"
