#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

PATTERN='QueryLakeBackend|/shared_folders/querylake_server/QueryLakeBackend|github.com/.*/QueryLakeBackend'

MATCHES="$(git grep -n -I -E "$PATTERN" -- . || true)"
if [[ -n "${MATCHES// }" ]]; then
  echo "ERROR: Legacy QueryLakeBackend reference(s) detected."
  echo "Please migrate to canonical QueryLake naming."
  echo
  echo "$MATCHES"
  exit 1
fi

echo "Legacy path/name guard passed."

