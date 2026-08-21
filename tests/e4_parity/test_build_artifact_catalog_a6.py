from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.e4_parity import build_artifact_catalog as builder
from tests.e4_parity.test_build_artifact_catalog import (
    GENERATED_AT,
    _write_catalog_fixture,
    _write_json,
)

LEDGER_PATH = "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"


def _make_binding_fixture_sync_eligible(paths: dict[str, Path]) -> list[Path]:
    ledger_path = paths["workspace"] / LEDGER_PATH
    _write_json(
        ledger_path,
        {
            "schema_version": "bb.e4.atomic_feature_ledger_seed.v1",
            "rows": [
                {
                    "feature_id": "feature_alpha",
                    "status": "accepted",
                    "points": 1,
                }
            ],
        },
    )
    inventory = json.loads(paths["inventory"].read_text(encoding="utf-8"))
    inventory["lanes"][0]["artifact_roles"]["atomic_feature_ledger"] = "lane_alpha:atomic_feature_ledger"
    _write_json(paths["inventory"], inventory)
    report_roles = json.loads(paths["report_roles"].read_text(encoding="utf-8"))
    report_roles["lane_artifact_roles"].append(
        {
            "lane_id": "lane_alpha",
            "role_key": "atomic_feature_ledger",
            "role_id": "lane_alpha:atomic_feature_ledger",
        }
    )
    _write_json(paths["report_roles"], report_roles)

    support_claim = json.loads(paths["support_claim"].read_text(encoding="utf-8"))
    support_claim["ledger_row_refs"] = [f"{LEDGER_PATH}#feature_alpha#sha256:{'0' * 64}"]
    support_claim["evidence_manifest_ref"] = "docs/conformance/support_claims/lane_alpha_v1_c4_evidence_manifest.json"
    _write_json(paths["support_claim"], support_claim)

    evidence_manifest = json.loads(paths["evidence_manifest"].read_text(encoding="utf-8"))
    evidence_manifest["artifacts"].append(
        {
            "role": "atomic_feature_ledger",
            "path": LEDGER_PATH,
            "sha256": "sha256:" + "1" * 64,
            "bytes": 1,
            "exists": False,
        }
    )
    _write_json(paths["evidence_manifest"], evidence_manifest)

    return [paths["support_claim"], paths["evidence_manifest"]]


def _run_catalog_cli(paths: dict[str, Path], *, write_bindings: bool = False) -> None:
    argv = [
        "--inventory",
        str(paths["inventory"]),
        "--report-roles",
        str(paths["report_roles"]),
        "--output",
        str(paths["output"]),
        "--generated-at-utc",
        GENERATED_AT,
    ]
    if write_bindings:
        argv.append("--write-bindings")

    assert builder.main(argv) == 0


def test_catalog_cli_without_write_bindings_preserves_sync_eligible_binding_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_only_paths = _write_catalog_fixture(tmp_path / "read_only", monkeypatch)
    watched_files = _make_binding_fixture_sync_eligible(read_only_paths)
    before = {path: path.read_bytes() for path in watched_files}

    _run_catalog_cli(read_only_paths)

    assert {path: path.read_bytes() for path in watched_files} == before

    opt_in_paths = _write_catalog_fixture(tmp_path / "opt_in", monkeypatch)
    opt_in_watched_files = _make_binding_fixture_sync_eligible(opt_in_paths)
    opt_in_before = {path: path.read_bytes() for path in opt_in_watched_files}

    _run_catalog_cli(opt_in_paths, write_bindings=True)

    assert opt_in_watched_files[0].read_bytes() != opt_in_before[opt_in_watched_files[0]]
    assert opt_in_watched_files[1].read_bytes() != opt_in_before[opt_in_watched_files[1]]
