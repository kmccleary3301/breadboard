from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.validate_engine_conformance_manifest import main as validate_manifest_main
from scripts.validate_kernel_contract_fixtures import _validate_manifest_and_fixtures


def _python_summary() -> Dict[str, Any]:
    manifest_path = ROOT / "conformance" / "engine_fixtures" / "python_reference_manifest_v1.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    fixture_root = ROOT / "conformance" / "engine_fixtures"
    fixture_families = sorted(path.name for path in fixture_root.iterdir() if path.is_dir())
    fixture_count = 0
    for family in fixture_families:
        family_root = fixture_root / family
        fixture_count += sum(1 for child in family_root.iterdir() if child.is_file() and child.suffix == ".json")
    return {
        "schemaVersion": "bb.kernel_conformance_summary.v1",
        "manifestRows": len(manifest.get("rows") or []),
        "comparatorClasses": sorted({str(row.get("comparatorClass")) for row in manifest.get("rows") or []}),
        "supportTiers": sorted({str(row.get("supportTier")) for row in manifest.get("rows") or []}),
        "fixtureFamilies": fixture_families,
        "fixtureCount": fixture_count,
    }


def _run(cmd: list[str], cwd: Path) -> None:
    subprocess.run(cmd, cwd=str(cwd), check=True)


def _ts_summary() -> Dict[str, Any]:
    contracts_dir = ROOT / "sdk" / "ts-kernel-contracts"
    core_dir = ROOT / "sdk" / "ts-kernel-core"
    env = dict(os.environ)
    env["BREADBOARD_REPO_ROOT"] = str(ROOT)
    _run(["npm", "install"], contracts_dir)
    _run(["npm", "run", "build"], contracts_dir)
    _run(["npm", "install"], core_dir)
    _run(["npm", "run", "build"], core_dir)
    completed = subprocess.run(
        ["node", "scripts/emit-conformance-summary.mjs"],
        cwd=str(core_dir),
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def main() -> int:
    if validate_manifest_main() != 0:
        return 1
    errors = _validate_manifest_and_fixtures()
    if errors:
        for error in errors:
            print(error)
        return 1
    python_summary = _python_summary()
    ts_summary = _ts_summary()
    if python_summary != ts_summary:
        print("python_summary=", json.dumps(python_summary, sort_keys=True))
        print("ts_summary=", json.dumps(ts_summary, sort_keys=True))
        return 1
    print("ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
