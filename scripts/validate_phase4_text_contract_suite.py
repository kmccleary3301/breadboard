#!/usr/bin/env python3
"""
Run strict text-contract validation across one or more phase4 capture runs.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from validate_phase4_claude_parity import DEFAULT_CONTRACT_FILE, validate_run
from validate_phase4_footer_contract import validate_footer_contract


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object JSON at {path}")
    return payload


def _discover_latest_runs(run_root: Path) -> list[Path]:
    if not run_root.exists() or not run_root.is_dir():
        return []
    out: list[Path] = []
    for scenario_dir in sorted(p for p in run_root.iterdir() if p.is_dir()):
        manifests = sorted(scenario_dir.rglob("scenario_manifest.json"))
        if not manifests:
            continue
        out.append(manifests[-1].parent)
    return out


def _load_contract(path: Path) -> dict[str, Any]:
    return _load_json(path)


def _contract_scenario_sets(contract: dict[str, Any], selected: str) -> set[str]:
    payload = contract.get("scenario_sets", {})
    if not isinstance(payload, dict):
        return set()
    if selected == "all":
        out: set[str] = set()
        for value in payload.values():
            if isinstance(value, list):
                out.update(str(v) for v in value if str(v).strip())
        return out
    raw = payload.get(selected, [])
    if not isinstance(raw, list):
        return set()
    return {str(v) for v in raw if str(v).strip()}


def _discover_run(path: Path) -> Path:
    root = path.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"run path not found: {root}")
    if root.is_file():
        root = root.parent
    if (root / "scenario_manifest.json").exists():
        return root
    manifests = sorted(root.rglob("scenario_manifest.json"))
    if manifests:
        return manifests[-1].parent
    raise FileNotFoundError(f"scenario_manifest.json not found under {root}")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate phase4 text-contract suite")
    p.add_argument(
        "--run-dir",
        action="append",
        default=[],
        help="run dir (or parent) to validate; may be provided multiple times",
    )
    p.add_argument(
        "--run-root",
        default="",
        help="scenario root to auto-select latest run from each scenario directory",
    )
    p.add_argument(
        "--scenario-set",
        choices=("hard_gate", "nightly", "all"),
        default="",
        help="optional scenario set from contract file (used with --run-root)",
    )
    p.add_argument(
        "--contract-file",
        default=str(DEFAULT_CONTRACT_FILE),
        help="phase4 text contract JSON file",
    )
    p.add_argument("--strict", action="store_true", help="treat parity warnings as errors")
    p.add_argument(
        "--fail-on-unmapped",
        action="store_true",
        help="fail suite when any run uses an unmapped scenario ID",
    )
    p.add_argument("--output-json", default="", help="optional output report path")
    return p.parse_args()


def main() -> int:
    try:
        args = parse_args()
        contract_file = Path(args.contract_file).expanduser().resolve()
        contract_payload = _load_contract(contract_file)
        selected_set = str(args.scenario_set or "").strip()
        selected_scenarios = _contract_scenario_sets(contract_payload, selected_set) if selected_set else set()

        run_dirs: list[Path] = []
        for raw in args.run_dir:
            run_dirs.append(_discover_run(Path(raw)))
        if args.run_root:
            run_dirs.extend(_discover_latest_runs(Path(args.run_root).expanduser().resolve()))

        # Deduplicate, preserve order.
        seen: set[str] = set()
        deduped: list[Path] = []
        for run in run_dirs:
            key = str(run.resolve())
            if key in seen:
                continue
            seen.add(key)
            deduped.append(run)
        run_dirs = deduped

        if not run_dirs:
            raise ValueError("no runs selected (use --run-dir and/or --run-root)")

        records: list[dict[str, Any]] = []
        overall_ok = True
        unmapped_count = 0
        for run_dir in run_dirs:
            parity = validate_run(run_dir, strict=bool(args.strict), contract_file=contract_file)
            footer = validate_footer_contract(run_dir)
            manifest = _load_json(run_dir / "scenario_manifest.json")
            scenario = str(manifest.get("scenario") or "")
            if selected_scenarios and scenario not in selected_scenarios:
                continue
            unmapped = any("unsupported scenario for parity validation" in err for err in parity.errors)
            if unmapped:
                unmapped_count += 1
            ok = bool(parity.ok and footer.ok and (not args.fail_on_unmapped or not unmapped))
            overall_ok = overall_ok and ok
            records.append(
                {
                    "run_dir": str(run_dir),
                    "scenario": scenario,
                    "ok": ok,
                    "unmapped": unmapped,
                    "parity": parity.to_dict(),
                    "footer": footer.to_dict(),
                }
            )

        missing_selected: list[str] = []
        if selected_scenarios:
            seen_scenarios = {str(r.get("scenario") or "") for r in records}
            for scenario in sorted(selected_scenarios):
                if scenario not in seen_scenarios:
                    missing_selected.append(scenario)
            if missing_selected:
                overall_ok = False

        report = {
            "schema_version": "phase4_text_contract_suite_v1",
            "overall_ok": overall_ok,
            "strict": bool(args.strict),
            "fail_on_unmapped": bool(args.fail_on_unmapped),
            "contract_file": str(contract_file),
            "scenario_set": selected_set or None,
            "total_runs": len(records),
            "unmapped_count": unmapped_count,
            "missing_selected_scenarios": missing_selected,
            "records": records,
        }

        out_path = (
            Path(args.output_json).expanduser().resolve()
            if args.output_json
            else Path.cwd() / "phase4_text_contract_suite_report.json"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
        if overall_ok:
            print(f"[phase4-text-suite] pass: {len(records)} runs")
            return 0
        print(f"[phase4-text-suite] fail: {len(records)} runs, unmapped={unmapped_count}")
        return 2
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[phase4-text-suite] error: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
