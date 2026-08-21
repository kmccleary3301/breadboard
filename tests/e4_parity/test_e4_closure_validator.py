from __future__ import annotations

import importlib
import json
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]
CLOSURE_SCHEMA_VERSION = "bb.e4.closure_report.v1"


ClosureBuilder = Callable[..., Mapping[str, Any]]


def _import_module(module_name: str) -> Any | None:
    try:
        return importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name == module_name:
            return None
        raise


def _closure_module() -> Any | None:
    return _import_module("scripts.e4_parity.validate_e4_closure")


def _require_closure_module() -> Any:
    module = _closure_module()
    if module is None:
        pytest.fail(
            "missing scripts.e4_parity.validate_e4_closure; B3 closure validator must expose a CLI/function "
            "that consolidates score and primitive-readiness validators"
        )
    return module


def _require_closure_builder() -> ClosureBuilder:
    module = _require_closure_module()
    candidate = getattr(module, "build_closure_report", None)
    if not callable(candidate):
        pytest.fail(
            "missing scripts.e4_parity.validate_e4_closure.build_closure_report; closure tests need a "
            "production helper that returns the same report the CLI writes"
        )
    return candidate


def _score_section_module() -> Any:
    module = _import_module("scripts.e4_parity.e4_closure_score_section")
    if module is None:
        pytest.fail("missing absorbed score-section helper module")
    return module


def _readiness_section_module() -> Any:
    module = _import_module("scripts.e4_parity.e4_closure_readiness_section")
    if module is None:
        pytest.fail("missing absorbed primitive-readiness-section helper module")
    return module


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _minimal_broken_case(tmp_path: Path) -> dict[str, Path]:
    repo_root = tmp_path / "breadboard_repo_integration_main_20260326"
    score_subledger_path = repo_root / "docs_tmp" / "phase_15" / "BB_E4_SCORE_SUBLEDGER.json"
    accepted_report_path = repo_root / "docs_tmp" / "phase_15" / "BB_E4_TARGET_SUPPORT_ACCEPTED_CLAIM_REPORT.json"
    readiness_ledger_path = repo_root / "docs_tmp" / "phase_15" / "BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"

    _write_json(
        score_subledger_path,
        {
            "schema_version": "not.bb.e4.score_subledger.v1",
            "current_accepted_points": 1,
            "target_support_claim_points": 0,
            "score_rows": [],
        },
    )
    _write_json(
        accepted_report_path,
        {
            "current_accepted_points": 2,
            "accepted_support_claims": [],
            "accepted_non_target_claims": [],
        },
    )
    _write_json(
        readiness_ledger_path,
        {
            "rows": [
                {
                    "schema_version": "bb.atomic_feature_ledger.v1",
                    "feature_id": "feat_closure_missing_primitive_contract",
                    "evidence_tier": "C4",
                    "promotion_state": "ready",
                    "e4_row_ref": "e4:synthetic/closure_missing_primitive_contract",
                    "breadboard_mapping": {
                        "primitive": "bb.synthetic.closure_missing_primitive.v1",
                        "support": "complete",
                        "truth_scope": "kernel_truth",
                    },
                    "fixture_refs": [],
                }
            ]
        },
    )
    return {
        "repo_root": repo_root,
        "score_subledger_path": score_subledger_path,
        "accepted_report_path": accepted_report_path,
        "readiness_ledger_path": readiness_ledger_path,
    }


def _legacy_score_report(paths: Mapping[str, Path]) -> Mapping[str, Any]:
    return _score_section_module().validate_score_subledger(
        subledger_path=paths["score_subledger_path"],
        accepted_report_path=paths["accepted_report_path"],
        repo_root=paths["repo_root"],
    )


def _legacy_readiness_report(paths: Mapping[str, Path]) -> Mapping[str, Any]:
    return _readiness_section_module().build_primitive_readiness_report(
        repo_root=paths["repo_root"],
        ledger_path=paths["readiness_ledger_path"],
        accepted_report_path=paths["accepted_report_path"],
    )


def _build_closure_report(paths: Mapping[str, Path], *, sections: Sequence[str] | None = None) -> Mapping[str, Any]:
    builder = _require_closure_builder()
    kwargs: dict[str, Any] = {
        "repo_root": paths["repo_root"],
        "subledger_path": paths["score_subledger_path"],
        "ledger_path": paths["readiness_ledger_path"],
        "accepted_report_path": paths["accepted_report_path"],
    }
    if sections is not None:
        kwargs["sections"] = tuple(sections)
    try:
        return builder(**kwargs)
    except TypeError as exc:
        raise AssertionError(
            "build_closure_report must accept repo_root, subledger_path, ledger_path, "
            "accepted_report_path, and optional sections keyword arguments"
        ) from exc


def _call_closure_main(argv: list[str]) -> int:
    module = _require_closure_module()
    main = getattr(module, "main", None)
    if not callable(main):
        pytest.fail("missing scripts.e4_parity.validate_e4_closure.main")
    try:
        result = main(argv)
    except SystemExit as exc:
        code = exc.code
        return int(code) if isinstance(code, int) else 1
    return int(result)


def _closure_argv(paths: Mapping[str, Path], *extra: str) -> list[str]:
    return [
        "--repo-root",
        str(paths["repo_root"]),
        "--subledger",
        str(paths["score_subledger_path"]),
        "--ledger",
        str(paths["readiness_ledger_path"]),
        "--accepted-report",
        str(paths["accepted_report_path"]),
        *extra,
    ]


def _assert_closure_header(report: Mapping[str, Any]) -> None:
    assert report["schema_version"] == CLOSURE_SCHEMA_VERSION
    assert isinstance(report["sections"], Mapping)
    assert isinstance(report["errors"], list)
    generated_at = report.get("generated_at_utc")
    assert isinstance(generated_at, str) and generated_at.endswith("Z")
    datetime.fromisoformat(generated_at.removesuffix("Z") + "+00:00")


def _assert_section_preserves_legacy_report(
    report: Mapping[str, Any],
    section_name: str,
    legacy_report: Mapping[str, Any],
) -> None:
    section = report["sections"][section_name]
    assert section["ok"] is legacy_report["ok"]
    assert section["errors"] == legacy_report["errors"]
    assert section["gate_errors"] == legacy_report["gate_errors"]
    assert section["pin_stale_count"] == legacy_report["pin_stale_count"]
    assert section["semantic_count"] == legacy_report["semantic_count"]
    assert section["error_count"] == legacy_report["error_count"]


def test_closure_builder_defaults_to_score_and_readiness_and_cli_writes_json_out(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The closure report defaults to both absorbed validators and --json-out writes the same JSON emitted by --json."""
    paths = _minimal_broken_case(tmp_path)
    score_report = _legacy_score_report(paths)
    readiness_report = _legacy_readiness_report(paths)

    report = _build_closure_report(paths)

    _assert_closure_header(report)
    assert set(report["sections"]) == {"score", "readiness"}
    _assert_section_preserves_legacy_report(report, "score", score_report)
    _assert_section_preserves_legacy_report(report, "readiness", readiness_report)
    assert report["ok"] is False
    assert report["errors"] == [*score_report["errors"], *readiness_report["errors"]]

    json_out = tmp_path / "closure_report.json"
    exit_code = _call_closure_main(_closure_argv(paths, "--json", "--json-out", str(json_out)))
    captured = capsys.readouterr()

    assert exit_code == 4
    assert captured.err == ""
    stdout_report = json.loads(captured.out)
    written_report = json.loads(json_out.read_text(encoding="utf-8"))
    assert written_report == stdout_report
    assert set(written_report["sections"]) == {"score", "readiness"}
    assert written_report["errors"] == [*score_report["errors"], *readiness_report["errors"]]


def test_sections_score_runs_only_score_and_preserves_score_error_semantics(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--sections score must not run readiness, and its verdict/errors must match the absorbed score validator."""
    paths = _minimal_broken_case(tmp_path)
    score_report = _legacy_score_report(paths)
    paths["readiness_ledger_path"].unlink()

    exit_code = _call_closure_main(_closure_argv(paths, "--sections", "score", "--json"))
    captured = capsys.readouterr()
    report = json.loads(captured.out)

    _assert_closure_header(report)
    assert exit_code == _score_section_module().gate_exit_code(score_report) == 4
    assert set(report["sections"]) == {"score"}
    _assert_section_preserves_legacy_report(report, "score", score_report)
    assert report["errors"] == score_report["errors"]
    assert captured.err == ""


def test_sections_readiness_runs_only_readiness_and_preserves_readiness_error_semantics(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--sections readiness must not run score, and its verdict/errors must match the absorbed readiness validator."""
    paths = _minimal_broken_case(tmp_path)
    readiness_report = _legacy_readiness_report(paths)
    paths["score_subledger_path"].unlink()

    exit_code = _call_closure_main(_closure_argv(paths, "--sections", "readiness", "--json"))
    captured = capsys.readouterr()
    report = json.loads(captured.out)

    _assert_closure_header(report)
    assert exit_code == _readiness_section_module().gate_exit_code(readiness_report) == 4
    assert set(report["sections"]) == {"readiness"}
    _assert_section_preserves_legacy_report(report, "readiness", readiness_report)
    assert report["errors"] == readiness_report["errors"]
    assert captured.err == ""


