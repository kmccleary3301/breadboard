#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$ROOT_DIR/.." && pwd)"
BATCH_REL="${1:-scenarios/p16/batches/vscode_final_core.json}"
BATCH="$(readlink -m "$ROOT_DIR/$BATCH_REL")"
OUT_ROOT="${P16_VSCODE_OUT_ROOT:-$REPO_ROOT/docs_tmp/cli_phase_6/CODESIGN_p16/implementation_validation_p16_final_design_complete/artifacts/host/vscode}"
OUT_ROOT="$(readlink -m "$OUT_ROOT")"
STAMP="${P16_HOST_STAMP:-$(date +%Y%m%d-%H%M%S)}"
RUN_DIR="$OUT_ROOT/vscode_final_core_$STAMP"
mkdir -p "$RUN_DIR/runs"
cd "$ROOT_DIR"

if [[ ! -f "$BATCH" ]]; then
  echo "missing VSCode batch: $BATCH" >&2
  exit 2
fi

mapfile -t SCENARIOS < <(node -e "const fs=require('fs'); const b=JSON.parse(fs.readFileSync(process.argv[1],'utf8')); for (const s of b.scenarios || []) console.log(s);" "$BATCH")
if [[ "${#SCENARIOS[@]}" -eq 0 ]]; then
  echo "VSCode batch has no scenarios: $BATCH" >&2
  exit 2
fi

cat > "$RUN_DIR/batch_start.json" <<JSON
{
  "schemaVersion": 1,
  "id": "p16_vscode_final_core",
  "batch": "$BATCH",
  "createdAt": "$(date -Iseconds)",
  "scenarioCount": ${#SCENARIOS[@]},
  "outRoot": "$OUT_ROOT",
  "runDir": "$RUN_DIR"
}
JSON

echo "[p16:vscode] preflight" | tee -a "$RUN_DIR/commands.log"
set +e
node tools/vscode-terminal-harness/preflight.cjs "$RUN_DIR/preflight" >"$RUN_DIR/preflight.log" 2>&1
PREFLIGHT_CODE=$?
set -e
if [[ "$PREFLIGHT_CODE" -ne 0 ]]; then
  cat > "$RUN_DIR/batch_manifest.json" <<JSON
{
  "schemaVersion": 1,
  "id": "p16_vscode_final_core",
  "ok": false,
  "redCount": 1,
  "failureClass": "ENV",
  "preflight": "$RUN_DIR/preflight/verdict.json",
  "message": "VSCode preflight failed; scenario runs were not attempted."
}
JSON
  echo "$RUN_DIR"
  exit "$PREFLIGHT_CODE"
fi

FAIL=0
printf 'scenario,exit_code,run_dir,status\n' > "$RUN_DIR/results.csv"
for scenario_rel in "${SCENARIOS[@]}"; do
  scenario_abs="$(readlink -m "$ROOT_DIR/$scenario_rel")"
  name="$(basename "$scenario_rel" .json)"
  log="$RUN_DIR/${name}.log"
  echo "[p16:vscode] running $scenario_rel" | tee -a "$RUN_DIR/commands.log"
  set +e
  P15_VSCODE_OUT_ROOT="$RUN_DIR/runs" bash scripts/qc_vscode_terminal_lane.sh "$scenario_abs" >"$log" 2>&1
  code=$?
  set -e
  scenario_run_dir="$(tail -n 1 "$log" || true)"
  status="pass"
  if [[ "$code" -ne 0 ]]; then
    status="fail"
    FAIL=1
  fi
  printf '%s,%s,%s,%s\n' "$scenario_rel" "$code" "$scenario_run_dir" "$status" >> "$RUN_DIR/results.csv"
  if [[ "$code" -ne 0 ]]; then
    echo "[p16:vscode] FAIL $scenario_rel exit=$code run=$scenario_run_dir" | tee -a "$RUN_DIR/commands.log"
  fi
done

node - "$RUN_DIR/results.csv" "$RUN_DIR/batch_manifest.json" <<'NODE'
const fs = require('fs');
const [csvPath, outPath] = process.argv.slice(2);
const lines = fs.readFileSync(csvPath, 'utf8').trim().split(/\n/).slice(1);
const results = lines.filter(Boolean).map(line => {
  const [scenario, exitCode, runDir, status] = line.split(',');
  return { scenario, exitCode: Number(exitCode), runDir, status };
});
const ok = results.length > 0 && results.every(r => r.status === 'pass');
fs.writeFileSync(outPath, JSON.stringify({
  schemaVersion: 1,
  id: 'p16_vscode_final_core',
  ok,
  redCount: results.filter(r => r.status !== 'pass').length,
  results,
}, null, 2));
NODE

if [[ "$FAIL" -ne 0 ]]; then
  echo "$RUN_DIR"
  exit 1
fi

echo "$RUN_DIR"
