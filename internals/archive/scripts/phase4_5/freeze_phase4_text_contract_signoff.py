#!/usr/bin/env python3
"""
Freeze a repeatable phase4 text-contract signoff report.

By default this script validates the latest discovered run per scenario for:
- hard_gate set (5 iterations)
- nightly set (3 iterations)

The output includes frozen run IDs (timestamped run-dir names) so reviewers can
audit exactly which artifacts were used for the signoff.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass, asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONTRACT_FILE = REPO_ROOT / "config" / "text_contracts" / "phase4_text_contract_v1.json"
DEFAULT_RUN_ROOT = REPO_ROOT / "docs_tmp" / "tmux_captures" / "scenarios" / "phase4_replay"
DEFAULT_OUT_JSON = REPO_ROOT / "docs_tmp" / "cli_phase_5" / "phase4_text_contract_signoff_v1.json"
DEFAULT_OUT_MD = REPO_ROOT / "docs_tmp" / "cli_phase_5" / "phase4_text_contract_signoff_v1.md"
SUITE_SCRIPT = REPO_ROOT / "scripts" / "validate_phase4_text_contract_suite.py"


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object JSON at {path}")
    return payload


def _scenario_set(contract_payload: dict[str, Any], set_name: str) -> list[str]:
    raw = contract_payload.get("scenario_sets", {}).get(set_name, [])
    if not isinstance(raw, list):
        raise ValueError(f"scenario set missing/not-list: {set_name}")
    return [str(x) for x in raw if str(x).strip()]


def _discover_latest_run_dir(run_root: Path, scenario: str) -> Path | None:
    base = run_root / scenario.replace("phase4_replay/", "", 1)
    if not base.exists():
        return None
    manifests = sorted(base.rglob("scenario_manifest.json"))
    if not manifests:
        return None
    return manifests[-1].parent


@dataclass
class IterationResult:
    iteration: int
    rc: int
    stdout: str
    report_json: str


def _run_suite_once(
    *,
    run_root: Path,
    contract_file: Path,
    scenario_set: str,
    out_json: Path,
) -> IterationResult:
    cmd = [
        "python",
        str(SUITE_SCRIPT),
        "--run-root",
        str(run_root),
        "--scenario-set",
        scenario_set,
        "--strict",
        "--fail-on-unmapped",
        "--contract-file",
        str(contract_file),
        "--output-json",
        str(out_json),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
    return IterationResult(
        iteration=0,
        rc=int(proc.returncode),
        stdout=(proc.stdout or proc.stderr or "").strip(),
        report_json=str(out_json),
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Freeze phase4 text-contract signoff report")
    p.add_argument("--run-root", default=str(DEFAULT_RUN_ROOT), help="phase4 replay scenario root")
    p.add_argument("--contract-file", default=str(DEFAULT_CONTRACT_FILE), help="text-contract JSON path")
    p.add_argument("--hard-iters", type=int, default=5, help="hard_gate iteration count")
    p.add_argument("--nightly-iters", type=int, default=3, help="nightly iteration count")
    p.add_argument("--out-json", default=str(DEFAULT_OUT_JSON), help="output JSON report")
    p.add_argument("--out-md", default=str(DEFAULT_OUT_MD), help="output Markdown report")
    return p.parse_args()


def _render_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# Phase4 Text-Contract Signoff (Frozen)")
    lines.append("")
    lines.append(f"- Generated at (UTC): `{report['generated_at_utc']}`")
    lines.append(f"- Contract: `{report['contract_file']}`")
    lines.append(f"- Run root: `{report['run_root']}`")
    lines.append(f"- Overall pass: `{report['overall_pass']}`")
    lines.append("")
    lines.append("## Frozen run IDs")
    lines.append("")
    lines.append("| Scenario | Run dir | Run ID |")
    lines.append("|---|---|---|")
    for row in report["frozen_runs"]:
        lines.append(f"| `{row['scenario']}` | `{row['run_dir']}` | `{row['run_id']}` |")
    lines.append("")
    lines.append("## Repeatability")
    lines.append("")
    for lane in ("hard_gate", "nightly"):
        runs = report["repeatability"][lane]
        lines.append(f"### {lane}")
        lines.append("")
        lines.append("| Iter | RC | Summary | Report |")
        lines.append("|---|---:|---|---|")
        for row in runs:
            summary = str(row.get("stdout") or "").replace("|", "\\|")
            lines.append(f"| {row['iteration']} | {row['rc']} | {summary} | `{row['report_json']}` |")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def main() -> int:
    args = parse_args()
    run_root = Path(args.run_root).expanduser().resolve()
    contract_file = Path(args.contract_file).expanduser().resolve()
    out_json = Path(args.out_json).expanduser().resolve()
    out_md = Path(args.out_md).expanduser().resolve()

    contract_payload = _load_json(contract_file)
    hard_scenarios = _scenario_set(contract_payload, "hard_gate")
    nightly_scenarios = _scenario_set(contract_payload, "nightly")
    frozen = []
    for scenario in sorted(set(hard_scenarios + nightly_scenarios)):
        run_dir = _discover_latest_run_dir(run_root, scenario)
        frozen.append(
            {
                "scenario": scenario,
                "run_dir": str(run_dir) if run_dir else "",
                "run_id": run_dir.name if run_dir else "",
                "present": bool(run_dir),
            }
        )

    repeatability: dict[str, list[dict[str, Any]]] = {"hard_gate": [], "nightly": []}

    for i in range(1, max(0, int(args.hard_iters)) + 1):
        report_path = Path("/tmp") / f"phase4_text_contract_signoff_hard_iter_{i}.json"
        row = _run_suite_once(
            run_root=run_root,
            contract_file=contract_file,
            scenario_set="hard_gate",
            out_json=report_path,
        )
        row.iteration = i
        repeatability["hard_gate"].append(asdict(row))

    for i in range(1, max(0, int(args.nightly_iters)) + 1):
        report_path = Path("/tmp") / f"phase4_text_contract_signoff_nightly_iter_{i}.json"
        row = _run_suite_once(
            run_root=run_root,
            contract_file=contract_file,
            scenario_set="nightly",
            out_json=report_path,
        )
        row.iteration = i
        repeatability["nightly"].append(asdict(row))

    overall_pass = all(item["present"] for item in frozen)
    overall_pass = overall_pass and all(row["rc"] == 0 for row in repeatability["hard_gate"])
    overall_pass = overall_pass and all(row["rc"] == 0 for row in repeatability["nightly"])

    report = {
        "schema_version": "phase4_text_contract_signoff_v1",
        "generated_at_utc": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "contract_file": str(contract_file),
        "run_root": str(run_root),
        "hard_iters": int(args.hard_iters),
        "nightly_iters": int(args.nightly_iters),
        "overall_pass": bool(overall_pass),
        "frozen_runs": frozen,
        "repeatability": repeatability,
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(_render_markdown(report), encoding="utf-8")

    print(f"[phase4-signoff] wrote {out_json}")
    print(f"[phase4-signoff] wrote {out_md}")
    if report["overall_pass"]:
        print("[phase4-signoff] pass")
        return 0
    print("[phase4-signoff] fail")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

