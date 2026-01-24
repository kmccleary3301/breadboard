#!/usr/bin/env bash
set -euo pipefail

# Build every Dockerfile in this folder.
# Usage: ./build_all.sh [sudo]

USE_SUDO=""
if [[ "${1:-}" == "sudo" ]]; then
  USE_SUDO="sudo"
fi

THIS_DIR="$(cd "$(dirname "$0")" && pwd)"

shopt -s nullglob
for df in "$THIS_DIR"/*.Dockerfile; do
  base="$(basename "$df")"
  tag="${base%.Dockerfile}"
  echo "Building image: ${tag} from ${base}"
  ${USE_SUDO} docker build -t "${tag}" -f "$df" "$THIS_DIR"
done
echo "All images built."


