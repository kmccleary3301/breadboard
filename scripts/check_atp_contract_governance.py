#!/usr/bin/env python3
from __future__ import annotations

import argparse
import fnmatch
import json
import re
from pathlib import Path
from typing import Any


ACR_RE = re.compile(r"^docs/contracts/policies/acr/ACR-[0-9]{8,}[-_].+\.md$")
ADR_RE = re.compile(r"^docs/contracts/policies/adr/ADR-[0-9]{8,}[-_].+\.md$")
ATP_CHANGE_GLOBS = [
    "docs/contracts/atp/schemas/*.json",
    "scripts/validate_atp_capability_contracts.py",
    "scripts/run_atp_retrieval_decomposition_loop_v1.py",
    "scripts/run_atp_specialist_solver_fallback_matrix_v1.py",
]


def _read_changed_files(path: Path | None, inline: list[str]) -> list[str]:
    rows = [str(item).strip() for item in inline if str(item).strip()]
    if path and path.exists():
        rows.extend([line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()])
    dedup: list[str] = []
    seen: set[str] = set()
    for row in rows:
        if row in seen:
            continue
        seen.add(row)
        dedup.append(row)
    return dedup


def _match_any(path: str, globs: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, glob) for glob in globs)


def evaluate(changed_files: list[str], pr_template_path: Path) -> dict[str, Any]:
    touched_atp = sorted([path for path in changed_files if _match_any(path, ATP_CHANGE_GLOBS)])
    touched_acr = sorted([path for path in changed_files if ACR_RE.match(path)])
    touched_adr = sorted([path for path in changed_files if ADR_RE.match(path)])

    template_text = pr_template_path.read_text(encoding="utf-8")
    checklist_binding = "ATP_CONTRACT_BREAK_CHECKLIST_V1.md" in template_text

    ok = (len(touched_atp) == 0) or (len(touched_acr) > 0 and len(touched_adr) > 0 and checklist_binding)
    failures: list[str] = []
    if touched_atp and len(touched_acr) == 0:
        failures.append("missing_acr_for_atp_contract_change")
    if touched_atp and len(touched_adr) == 0:
        failures.append("missing_adr_for_atp_contract_change")
    if touched_atp and not checklist_binding:
        failures.append("pr_template_missing_atp_contract_break_checklist_binding")

    return {
        "schema": "breadboard.atp_contract_governance_report.v1",
        "ok": ok,
        "changed_files_count": len(changed_files),
        "touched_atp_contract_paths": touched_atp,
        "touched_acr_paths": touched_acr,
        "touched_adr_paths": touched_adr,
        "pr_template_path": str(pr_template_path),
        "pr_template_has_atp_contract_break_checklist_binding": checklist_binding,
        "failures": failures,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--changed-files-file", default="")
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--pr-template", default=".github/PULL_REQUEST_TEMPLATE.md")
    parser.add_argument("--json-out", default="artifacts/atp_governance/atp_contract_governance_report.latest.json")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    changed = _read_changed_files(
        Path(args.changed_files_file).resolve() if args.changed_files_file else None,
        list(args.changed_file),
    )
    report = evaluate(changed_files=changed, pr_template_path=Path(args.pr_template).resolve())
    out_path = Path(args.json_out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"[atp-contract-governance] ok={report['ok']} atp_changes={len(report['touched_atp_contract_paths'])}")
        if not report["ok"]:
            for failure in report["failures"]:
                print(f"- {failure}")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
