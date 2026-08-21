from __future__ import annotations

import copy
import hashlib
import importlib
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[2]
PRIMITIVE = "bb.effective_config_graph.v1"
E4_ROW_REF = "e4:pi/config/global_project_merge"
FEATURE_ID = "feat_pi_config_global_project_merge"

PathValidator = Callable[..., list[str]]


def _validator_module() -> Any | None:
    return importlib.import_module("scripts.e4_parity.e4_closure_readiness_section")


def _path_validator() -> PathValidator | None:
    module = _validator_module()
    if module is None:
        return None
    candidate = getattr(module, "collect_primitive_readiness_errors", None)
    return candidate if callable(candidate) else None


def _require_path_validator() -> PathValidator:
    validator = _path_validator()
    if validator is None:
        pytest.fail(
            "missing scripts.e4_parity.e4_closure_readiness_section.collect_primitive_readiness_errors; "
            "primitive-readiness tests need a production helper that rejects ready C4 rows whose primitive contract "
            "or row-scoped C4 evidence is absent/stale"
        )
    return validator


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _row_hash(row_id: str, row: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        {"row_id": row_id, "row": row},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _write_json(path: Path, payload: Mapping[str, Any] | list[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _artifact_ref(path: Path, repo_root: Path) -> str:
    return f"{_relative(path, repo_root)}#{_sha256_file(path)}"


def _primitive_schema() -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"https://breadboard.local/contracts/{PRIMITIVE}.schema.json",
        "type": "object",
        "properties": {"schema_version": {"const": PRIMITIVE}},
        "required": ["schema_version"],
        "additionalProperties": True,
    }


def _ready_c4_row(fixture_refs: list[str]) -> dict[str, Any]:
    return {
        "schema_version": "bb.atomic_feature_ledger.v1",
        "feature_id": FEATURE_ID,
        "dedupe_key": "pi/config/settings_global_project_merge/model_visible_yes/stateful_yes",
        "target": "pi",
        "family": "config",
        "claim_type": "source",
        "evidence_tier": "C4",
        "source_refs": ["source:pi/settings-manager.ts:10-80"],
        "model_visible": True,
        "stateful": True,
        "breadboard_mapping": {
            "primitive": PRIMITIVE,
            "support": "partial",
            "truth_scope": "kernel_truth",
        },
        "gap_kind": "evidence",
        "fixture_refs": fixture_refs,
        "e4_row_ref": E4_ROW_REF,
        "promotion_state": "ready",
    }


def _write_case(tmp_path: Path) -> dict[str, Any]:
    repo_root = tmp_path / "breadboard_repo_integration_main_20260326"
    schema_path = repo_root / "contracts" / "kernel" / "schemas" / f"{PRIMITIVE}.schema.json"
    _write_json(schema_path, _primitive_schema())

    freeze_row = {
        "id": E4_ROW_REF,
        "target": "pi",
        "lane_id": "global_project_merge",
        "target_version": "synthetic-current",
    }
    freeze_manifest = repo_root / "config" / "e4_target_freeze_manifest.yaml"
    _write_text(
        freeze_manifest,
        "e4_configs:\n"
        f"  {E4_ROW_REF}:\n"
        f"    id: {E4_ROW_REF}\n"
        "    target: pi\n"
        "    lane_id: global_project_merge\n"
        "    target_version: synthetic-current\n",
    )
    freeze_ref = f"{_relative(freeze_manifest, repo_root)}#{E4_ROW_REF}#{_row_hash(E4_ROW_REF, freeze_row)}"

    support_claim = repo_root / "docs" / "conformance" / "support_claims" / "global_project_merge_support_claim.json"
    evidence_manifest = repo_root / "docs" / "conformance" / "support_claims" / "global_project_merge_evidence_manifest.json"
    capture = repo_root / "docs" / "conformance" / "e4_target_support" / "global_project_merge" / "raw_capture_manifest.json"
    replay = repo_root / "docs" / "conformance" / "e4_target_support" / "global_project_merge" / "bb_replay_result.json"
    comparator = repo_root / "docs" / "conformance" / "e4_target_support" / "global_project_merge" / "comparator_report.json"
    node_gate = repo_root / "artifacts" / "conformance" / "node_gate" / "global_project_merge_c4_node_gate.json"

    _write_json(capture, {"schema_version": "bb.e4.raw_capture_manifest.v1", "e4_row_ref": E4_ROW_REF})
    _write_json(replay, {"schema_version": "bb.e4.replay_result.v1", "e4_row_ref": E4_ROW_REF, "ok": True})
    _write_json(
        comparator,
        {
            "schema_version": "bb.e4.comparator_report.v1",
            "e4_row_ref": E4_ROW_REF,
            "ok": True,
            "differences": [],
        },
    )
    _write_json(
        evidence_manifest,
        {
            "schema_version": "bb.e4.evidence_manifest.v1",
            "claim_id": "global_project_merge_support_claim",
            "artifacts": [
                {"role": "capture", "path": _relative(capture, repo_root), "sha256": _sha256_file(capture)},
                {"role": "replay", "path": _relative(replay, repo_root), "sha256": _sha256_file(replay)},
                {"role": "comparator", "path": _relative(comparator, repo_root), "sha256": _sha256_file(comparator)},
            ],
        },
    )
    _write_json(
        support_claim,
        {
            "schema_version": "bb.e4.support_claim.v1",
            "claim_id": "global_project_merge_support_claim",
            "accepted": True,
            "evidence_manifest_ref": _relative(evidence_manifest, repo_root),
            "ledger_row_refs": [],
            "validation_refs": [],
        },
    )
    _write_json(
        node_gate,
        {
            "schema_version": "bb.e4.c4_chain_validation_report.v1",
            "ok": True,
            "accepted": True,
            "config_id": E4_ROW_REF,
            "hashes": {
                "support_claim": _sha256_file(support_claim),
                "evidence_manifest": _sha256_file(evidence_manifest),
                "comparator": _sha256_file(comparator),
            },
            "errors": [],
        },
    )

    fixture_refs = [
        f"freeze:{freeze_ref}",
        f"capture:{_artifact_ref(capture, repo_root)}",
        f"replay:{_artifact_ref(replay, repo_root)}",
        f"comparator:{_artifact_ref(comparator, repo_root)}",
        f"support_claim:{_relative(support_claim, repo_root)}",
        f"evidence_manifest:{_relative(evidence_manifest, repo_root)}",
    ]
    row = _ready_c4_row(fixture_refs)
    ledger_path = repo_root / "docs_tmp" / "phase_15" / "BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
    _write_json(ledger_path, {"rows": [row]})

    support_payload = json.loads(support_claim.read_text(encoding="utf-8"))
    support_payload["ledger_row_refs"] = [
        f"{_relative(ledger_path, repo_root)}#{FEATURE_ID}#{_row_hash(FEATURE_ID, row)}"
    ]
    support_payload["validation_refs"] = [_artifact_ref(node_gate, repo_root)]
    _write_json(support_claim, support_payload)

    _write_json(
        node_gate,
        {
            "schema_version": "bb.e4.c4_chain_validation_report.v1",
            "ok": True,
            "accepted": True,
            "config_id": E4_ROW_REF,
            "hashes": {
                "support_claim": _sha256_file(support_claim),
                "evidence_manifest": _sha256_file(evidence_manifest),
                "comparator": _sha256_file(comparator),
            },
            "errors": [],
        },
    )
    accepted_report_path = repo_root / "docs_tmp" / "phase_15" / "BB_E4_TARGET_SUPPORT_ACCEPTED_CLAIM_REPORT.json"
    accepted_report = {
        "schema_version": "bb.e4.accepted_report.v1",
        "accepted_support_claims": [
            {
                "claim_ref": _relative(support_claim, repo_root),
                "evidence_manifest_ref": _relative(evidence_manifest, repo_root),
                "live_validator_ref": _artifact_ref(node_gate, repo_root),
                "claim_sha256": _sha256_file(support_claim),
                "evidence_manifest_sha256": _sha256_file(evidence_manifest),
                "scope": {"config_id": E4_ROW_REF},
            }
        ],
    }
    _write_json(accepted_report_path, accepted_report)

    return {
        "repo_root": repo_root,
        "schema_path": schema_path,
        "ledger_path": ledger_path,
        "accepted_report_path": accepted_report_path,
        "accepted_report": accepted_report,
        "row": row,
        "artifacts": {
            "support_claim": support_claim,
            "evidence_manifest": evidence_manifest,
            "capture": capture,
            "replay": replay,
            "comparator": comparator,
            "node_gate": node_gate,
        },
    }


def _call_validator(paths: Mapping[str, Any]) -> list[str]:
    validator = _require_path_validator()
    return [
        str(error)
        for error in validator(
            repo_root=paths["repo_root"],
            ledger_path=paths["ledger_path"],
            accepted_report_path=paths["accepted_report_path"],
        )
    ]


def _write_ledger_row(paths: Mapping[str, Any], row: Mapping[str, Any]) -> None:
    _write_json(paths["ledger_path"], {"rows": [row]})


def _write_accepted_report(paths: Mapping[str, Any], accepted_report: Mapping[str, Any]) -> None:
    _write_json(paths["accepted_report_path"], accepted_report)


def _assert_invalid(paths: Mapping[str, Any], *needles: str) -> None:
    errors = _call_validator(paths)
    assert errors, "broken primitive-readiness evidence was accepted"
    joined = "\n".join(errors).lower()
    for needle in needles:
        assert needle.lower() in joined


def test_ready_c4_row_with_existing_primitive_schema_and_current_c4_refs_passes(tmp_path: Path) -> None:
    """A ready C4 ledger row is primitive-ready when the primitive contract exists and every row-scoped C4 ref is hash-current."""
    paths = _write_case(tmp_path)

    assert _call_validator(paths) == []


def test_ready_c4_row_with_missing_primitive_schema_is_rejected(tmp_path: Path) -> None:
    """A ready C4 ledger row cannot be accepted for a primitive whose schema file is absent."""
    paths = _write_case(tmp_path)
    paths["schema_path"].unlink()

    _assert_invalid(paths, PRIMITIVE, "schema")


@pytest.mark.parametrize(
    ("missing_kind", "expected_needles"),
    [
        ("support_claim", ("support", "fixture")),
        ("evidence_manifest", ("evidence", "fixture")),
        ("comparator", ("comparator", "fixture")),
    ],
)
def test_ready_c4_row_lacking_row_scoped_c4_fixture_ref_is_rejected(
    tmp_path: Path,
    missing_kind: str,
    expected_needles: tuple[str, ...],
) -> None:
    """Support, evidence, and comparator refs must be row-scoped fixtures, not implied by a broad report."""
    paths = _write_case(tmp_path)
    row = copy.deepcopy(paths["row"])
    row["fixture_refs"] = [ref for ref in row["fixture_refs"] if not str(ref).startswith(f"{missing_kind}:")]
    _write_ledger_row(paths, row)

    _assert_invalid(paths, *expected_needles)


@pytest.mark.parametrize("node_gate_mutation", ["missing_ref", "failed_report"])
def test_ready_c4_row_requires_passed_node_gate_validator_for_same_e4_row_ref(
    tmp_path: Path,
    node_gate_mutation: str,
) -> None:
    """A ready C4 row needs a passed node_gate report tied to the row's own e4_row_ref."""
    paths = _write_case(tmp_path)
    accepted_report = copy.deepcopy(paths["accepted_report"])
    accepted_claim = accepted_report["accepted_support_claims"][0]

    if node_gate_mutation == "missing_ref":
        accepted_claim.pop("live_validator_ref")
        _write_accepted_report(paths, accepted_report)
        _assert_invalid(paths, "live_validator_ref", "missing")
        return

    node_gate_path = paths["artifacts"]["node_gate"]
    node_gate_payload = json.loads(node_gate_path.read_text(encoding="utf-8"))
    node_gate_payload["ok"] = False
    node_gate_payload["accepted"] = False
    node_gate_payload["errors"] = ["synthetic C4 failure"]
    _write_json(node_gate_path, node_gate_payload)
    accepted_claim["live_validator_ref"] = _artifact_ref(node_gate_path, paths["repo_root"])
    _write_accepted_report(paths, accepted_report)

    _assert_invalid(paths, "live_validator_ref", "ok=true", "accepted=true")
