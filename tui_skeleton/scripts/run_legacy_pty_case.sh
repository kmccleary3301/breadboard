#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

cat >&2 <<'NOTICE'
[qc][legacy-compat] This entrypoint is legacy compatibility only.
[qc][legacy-compat] Prefer a canonical profile from scripts/qc_profile_matrix.sh when one exists.
[qc][legacy-compat] This wrapper intentionally routes direct PTY-case usage through the compatibility harness with an explicit warning.
NOTICE

exec node --import tsx scripts/repl_pty_harness.ts "$@"
