#!/usr/bin/env bash
set -euo pipefail

pane_id="${1:-}"
out_dir="${2:-.}"
line_count="${3:-}"

if [[ -z "${pane_id}" ]]; then
  echo "Usage: scripts/capture_tmux_pane.sh <pane_id> [out_dir] [line_count]" >&2
  exit 1
fi

timestamp="$(date +"%Y%m%d-%H%M%S")"
safe_pane="${pane_id//[^a-zA-Z0-9]/_}"
mkdir -p "${out_dir}"
ansi_path="${out_dir}/tmux_${safe_pane}_${timestamp}.ansi"
svg_path="${ansi_path%.ansi}.svg"

if [[ -n "${line_count}" ]]; then
  tmux capture-pane -ep -t "${pane_id}" -S "-${line_count}" > "${ansi_path}"
else
  tmux capture-pane -ep -t "${pane_id}" -S - > "${ansi_path}"
fi
node --import tsx scripts/ansi_to_svg.ts "${ansi_path}" "${svg_path}"
echo "[tmux] wrote ${ansi_path} and ${svg_path}"
