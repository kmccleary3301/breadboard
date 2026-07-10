#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"
RAW_CAPTURE="${1:-scripts/_tmp_qc_scrollback_control_sequences.raw}"
SNAPSHOT_CAPTURE="${RAW_CAPTURE%.raw}.snapshots.txt"
LAUNCHER="$(mktemp "${ROOT_DIR}/scripts/_tmp_scrollback_control_launcher.XXXXXX.sh")"
cleanup() {
  rm -f "$LAUNCHER"
}
trap cleanup EXIT
rm -f "$RAW_CAPTURE"
cat > "$LAUNCHER" <<'SH'
#!/usr/bin/env bash
set -euo pipefail
printf '%s\n' PRE_BB_HISTORY_SENTINEL
exec node dist/main.js repl --tui classic
SH
chmod +x "$LAUNCHER"
node --import tsx scripts/repl_pty_harness.ts \
  --script scripts/qc_cases/startup_landing.json \
  --cmd "$LAUNCHER" \
  --snapshots "$SNAPSHOT_CAPTURE" \
  --raw-output "$RAW_CAPTURE" \
  --max-duration-ms 120000 >/dev/null
node --import tsx scripts/qc_scrollback_control_sequences_gate.ts "$RAW_CAPTURE"
