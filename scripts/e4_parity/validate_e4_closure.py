#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    from scripts.e4_parity.e4_closure_score_section import (
        DEFAULT_ACCEPTED_REPORT,
        DEFAULT_SUBLEDGER,
        ROOT,
        validate_score_subledger,
    )
    from scripts.e4_parity.e4_closure_readiness_section import (
        DEFAULT_LEDGER,
        build_primitive_readiness_report,
    )
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from e4_closure_score_section import (  # type: ignore
        DEFAULT_ACCEPTED_REPORT,
        DEFAULT_SUBLEDGER,
        ROOT,
        validate_score_subledger,
    )
    from e4_closure_readiness_section import (  # type: ignore
        DEFAULT_LEDGER,
        build_primitive_readiness_report,
    )


def _load_json(path: Path | str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))

SCHEMA_VERSION = "bb.e4.closure_report.v1"
DEFAULT_SECTIONS = ("score", "readiness")


def _utc_now() -> str:
    return "2026-07-03T00:00:00Z"


def _parse_sections(value: str | Sequence[str] | None) -> tuple[str, ...]:
    if value is None:
        return DEFAULT_SECTIONS
    if isinstance(value, str):
        raw = [part.strip() for part in value.split(",")]
    else:
        raw = [str(part).strip() for part in value]
    sections = tuple(part for part in raw if part)
    unknown = sorted(set(sections) - set(DEFAULT_SECTIONS))
    if unknown:
        raise ValueError(f"unknown closure section(s): {', '.join(unknown)}")
    return sections or DEFAULT_SECTIONS


def _section_counts(section_reports: Mapping[str, Mapping[str, Any]]) -> tuple[int, int, int]:
    pin_stale = 0
    semantic = 0
    errors = 0
    for report in section_reports.values():
        pin_stale += int(report.get("pin_stale_count") or 0)
        semantic += int(report.get("semantic_count") or 0)
        errors += int(report.get("error_count") or len(report.get("errors") or []))
    return errors, pin_stale, semantic


def build_closure_report(
    *,
    sections: str | Sequence[str] | None = None,
    repo_root: Path | str = ROOT,
    subledger_path: Path | str = DEFAULT_SUBLEDGER,
    ledger_path: Path | str = DEFAULT_LEDGER,
    accepted_report_path: Path | str = DEFAULT_ACCEPTED_REPORT,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    selected = _parse_sections(sections)
    accepted_report = _load_json(accepted_report_path)
    section_reports: dict[str, Mapping[str, Any]] = {}
    if "score" in selected:
        section_reports["score"] = validate_score_subledger(
            subledger_path=subledger_path,
            accepted_report_path=accepted_report_path,
            repo_root=repo_root,
            subledger_data=_load_json(subledger_path),
            accepted_report_data=accepted_report,
        )
    if "readiness" in selected:
        section_reports["readiness"] = build_primitive_readiness_report(
            repo_root=repo_root,
            ledger_path=ledger_path,
            accepted_report_path=accepted_report_path,
            ledger_data=_load_json(ledger_path),
            accepted_report_data=accepted_report,
        )

    errors: list[str] = []
    gate_errors: list[Mapping[str, Any]] = []
    for report in section_reports.values():
        errors.extend(str(error) for error in (report.get("errors") or []))
        gate_errors.extend(error for error in (report.get("gate_errors") or []) if isinstance(error, Mapping))
    error_count, pin_stale_count, semantic_count = _section_counts(section_reports)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": generated_at_utc or _utc_now(),
        "sections": {name: dict(report) for name, report in section_reports.items()},
        "errors": errors,
        "gate_errors": [dict(error) for error in gate_errors],
        "pin_stale_count": pin_stale_count,
        "semantic_count": semantic_count,
        "error_count": error_count,
        "ok": error_count == 0,
    }


def closure_exit_code(report: Mapping[str, Any]) -> int:
    if report.get("ok"):
        return 0
    if int(report.get("semantic_count") or 0):
        return 4
    return 3


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate E4 closure score/readiness invariants.")
    parser.add_argument("--subledger", default=str(DEFAULT_SUBLEDGER))
    parser.add_argument("--ledger", default=str(DEFAULT_LEDGER))
    parser.add_argument("--accepted-report", default=str(DEFAULT_ACCEPTED_REPORT))
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--sections", default=",".join(DEFAULT_SECTIONS))
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--json-out", default=None)
    args = parser.parse_args(argv)

    try:
        report = build_closure_report(
            sections=args.sections,
            repo_root=args.repo_root,
            subledger_path=args.subledger,
            ledger_path=args.ledger,
            accepted_report_path=args.accepted_report,
        )
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 4

    if args.json_out:
        out = Path(args.json_out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    elif report["ok"]:
        print("ok")
    else:
        for error in report["errors"]:
            print(error, file=sys.stderr)
    return closure_exit_code(report)


if __name__ == "__main__":
    raise SystemExit(main())
