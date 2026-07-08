from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from scripts.e4_parity.validate_atomic_feature_ledger import collect_atomic_feature_ledger_errors


ROOT = Path(__file__).resolve().parents[2]


def _fixture_ref(kind: str, path: str) -> str:
    digest = hashlib.sha256(f"{kind}:{path}".encode("utf-8")).hexdigest()
    return f"{kind}:{path}#sha256:{digest}"


def _c4_fixture_refs(*kinds: str) -> list[str]:
    paths = {
        "freeze": "target_freeze_row.yaml",
        "capture": "raw_capture.json",
        "replay": "breadboard_replay_fixture.json",
        "comparator": "strict_comparator_output.json",
    }
    return [_fixture_ref(kind, paths[kind]) for kind in kinds]



def _valid_row() -> dict[str, object]:
    return {
        "schema_version": "bb.atomic_feature_ledger.v1",
        "feature_id": "feat_pi_config_global_project_merge",
        "dedupe_key": "pi/config/settings_global_project_merge/model_visible_yes/stateful_yes",
        "target": "pi",
        "family": "config",
        "claim_type": "source",
        "evidence_tier": "C4",
        "source_refs": ["source:pi/settings-manager.ts:10-80"],
        "model_visible": True,
        "stateful": True,
        "breadboard_mapping": {
            "primitive": "bb.effective_config_graph.v1",
            "support": "partial",
            "truth_scope": "kernel_truth",
        },
        "gap_kind": "evidence",
        "fixture_refs": _c4_fixture_refs("freeze", "capture", "replay", "comparator"),
        "e4_row_ref": "e4:pi/config/global_project_merge",
        "promotion_state": "ready",
    }


def _errors_for(row: dict[str, object]) -> list[str]:
    return collect_atomic_feature_ledger_errors(row)


def test_valid_row_and_minimal_example_validate() -> None:
    assert _errors_for(_valid_row()) == []

    example_path = ROOT / "contracts" / "kernel" / "examples" / "atomic_feature_ledger_minimal.json"
    example = json.loads(example_path.read_text(encoding="utf-8"))
    assert collect_atomic_feature_ledger_errors(example) == []


def test_missing_required_field_is_rejected() -> None:
    row = _valid_row()
    row.pop("feature_id")

    errors = _errors_for(row)

    assert any("feature_id" in error and "required" in error for error in errors)


def test_c2_missing_source_is_rejected() -> None:
    row = _valid_row()
    row["evidence_tier"] = "C2"
    row["source_refs"] = []
    row["fixture_refs"] = []
    row["e4_row_ref"] = None
    row["promotion_state"] = "candidate"

    errors = _errors_for(row)

    assert any("C2 evidence requires at least one source_ref" in error for error in errors)


def test_c4_missing_required_lineage_ref_is_rejected() -> None:
    row = _valid_row()
    row["fixture_refs"] = _c4_fixture_refs("freeze", "capture", "replay")

    errors = _errors_for(row)

    assert any("comparator" in error for error in errors)


def test_product_name_cannot_be_primitive() -> None:
    row = _valid_row()
    mapping = copy.deepcopy(row["breadboard_mapping"])
    assert isinstance(mapping, dict)
    mapping["primitive"] = "pi"
    row["breadboard_mapping"] = mapping

    errors = _errors_for(row)

    assert any("product name" in error for error in errors)


def test_registry_discovery_cannot_be_model_visible_exposure() -> None:
    row = _valid_row()
    row["claim_type"] = "registry_discovery"
    row["model_visible"] = True

    errors = _errors_for(row)

    assert any("registry discovery" in error for error in errors)


def test_projection_cannot_be_kernel_truth() -> None:
    row = _valid_row()
    row["family"] = "projection"
    row["claim_type"] = "host_projection"
    mapping = copy.deepcopy(row["breadboard_mapping"])
    assert isinstance(mapping, dict)
    mapping["primitive"] = "bb.kernel_event.v1"
    mapping["truth_scope"] = "kernel_truth"
    row["breadboard_mapping"] = mapping

    errors = _errors_for(row)

    assert any("kernel truth" in error for error in errors)


def test_raw_secret_key_is_rejected() -> None:
    row = _valid_row()
    row["source_refs"] = ["source:pi/settings-manager.ts api_key=sk-AAAAAAAAAAAAAAAAAAAAAAAA"]

    errors = _errors_for(row)

    assert any("raw secret" in error for error in errors)
