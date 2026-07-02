#!/usr/bin/env bash
set -euo pipefail

artifact_dir="${1:-artifacts/conformance}"
manifest="${CT_SCENARIOS_MANIFEST:-docs/conformance/ct_scenarios_v1.json}"
matrix_csv="${CONFORMANCE_MATRIX_CSV:-docs/conformance/CONFORMANCE_TEST_MATRIX_V1.csv}"
python_bin="${PYTHON:-python3}"


mkdir -p "${artifact_dir}/node_gate" "${artifact_dir}/junit"

set +e
"${python_bin}" scripts/run_ct_scenarios.py \
  --manifest "${manifest}" \
  --json-out "${artifact_dir}/ct_scenarios_result_v1.json" \
  --rows-out "${artifact_dir}/ct_scenarios_rows_v1.json"
ct_exit=$?
set -e

"${python_bin}" scripts/sync_conformance_matrix_status.py \
  --matrix-csv "${matrix_csv}" \
  --rows-json "${artifact_dir}/ct_scenarios_rows_v1.json" \
  --out-csv "${artifact_dir}/CONFORMANCE_TEST_MATRIX_V1.synced.csv" \
  --summary-json "${artifact_dir}/conformance_matrix_sync_summary_v1.json" \
  --summary-md "${artifact_dir}/conformance_matrix_sync_summary_v1.md"

"${python_bin}" - "$artifact_dir" "$ct_exit" <<'PY'
import json
import sys
from pathlib import Path

artifact_dir = Path(sys.argv[1])
ct_exit = int(sys.argv[2])
result = json.loads((artifact_dir / "ct_scenarios_result_v1.json").read_text(encoding="utf-8"))
summary = json.loads((artifact_dir / "conformance_matrix_sync_summary_v1.json").read_text(encoding="utf-8"))
print(
    "[ct-scenarios] "
    f"exit={ct_exit} status={result['status']} scenarios={result['scenario_count']} "
    f"passed={result['passing_count']} not_implemented={result['not_implemented_count']} "
    f"failures={result['failing_count']}"
)
print(
    "[sync-conformance-matrix] "
    f"mapped={summary['mapped_rows']}/{summary['matrix_row_count']} "
    f"passing={summary['passing_rows']} failing={summary['failing_rows']} "
    f"not_implemented={summary['not_implemented_rows']} "
    f"blocking_not_implemented={summary['blocking_not_implemented_rows']}"
)
if ct_exit != 0 or not result["ok"] or not summary["ok"]:
    raise SystemExit(1)
PY
