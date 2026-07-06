from __future__ import annotations

import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from breadboard.rl.adapters.benchflow import build_benchflow_fixture_probe_report  # noqa: E402
from breadboard.rl.adapters.ors import build_ors_fixture_probe_report  # noqa: E402
from breadboard.rl.adapters.prime_verifiers import build_prime_verifiers_fixture_probe_report  # noqa: E402
from breadboard.rl.adapters.probe import validate_adapter_probe_report  # noqa: E402
from breadboard.rl.adapters.verl import build_verl_jsonl_probe_report  # noqa: E402


def main() -> None:
    output_dir = Path("../docs_tmp/ZYPHRA/RL_PHASE_1/runs/m9_adapter_probes")
    output_dir.mkdir(parents=True, exist_ok=True)
    reports = [
        build_benchflow_fixture_probe_report(),
        build_ors_fixture_probe_report(),
        build_verl_jsonl_probe_report(),
        build_prime_verifiers_fixture_probe_report(),
    ]
    summary = []
    for report in reports:
        errors = validate_adapter_probe_report(report)
        if errors:
            raise SystemExit(f"{report.adapter_id} invalid: {errors}")
        path = output_dir / f"{report.adapter_id}.json"
        path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
        summary.append(report.to_dict())
    (output_dir / "adapter_probe_summary.json").write_text(
        json.dumps({"reports": summary}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("reports=" + str(len(reports)) + " adapters=" + ",".join(report.adapter_id for report in reports))


if __name__ == "__main__":
    main()
