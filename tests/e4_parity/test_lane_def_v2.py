from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator, RefResolver
from jsonschema.exceptions import ValidationError

from scripts.e4_parity.lane_definitions import LaneDefValidationError, load_lane_def


ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DIR = ROOT / "contracts" / "kernel" / "schemas"
LANE_DEF_V2_SCHEMA_PATH = SCHEMA_DIR / "bb.e4.lane_def.v2.schema.json"
KERNEL_COMMON_SCHEMA_PATH = SCHEMA_DIR / "bb.kernel.common.v1.schema.json"
E4_COMMON_SCHEMA_PATH = SCHEMA_DIR / "bb.e4.common.v1.schema.json"


def _load_json(path: Path) -> dict[str, Any]:
    import json

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


@pytest.fixture(scope="module")
def lane_def_v2_validator() -> Draft202012Validator:
    assert LANE_DEF_V2_SCHEMA_PATH.is_file(), "missing contracts/kernel/schemas/bb.e4.lane_def.v2.schema.json"
    schema = _load_json(LANE_DEF_V2_SCHEMA_PATH)
    kernel_common = _load_json(KERNEL_COMMON_SCHEMA_PATH)
    e4_common = _load_json(E4_COMMON_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)
    store = {
        str(schema["$id"]): schema,
        LANE_DEF_V2_SCHEMA_PATH.name: schema,
        str(kernel_common["$id"]): kernel_common,
        KERNEL_COMMON_SCHEMA_PATH.name: kernel_common,
        str(e4_common["$id"]): e4_common,
        E4_COMMON_SCHEMA_PATH.name: e4_common,
    }
    return Draft202012Validator(schema, resolver=RefResolver.from_schema(schema, store=store))


def _schema_errors(validator: Draft202012Validator, payload: dict[str, Any]) -> list[ValidationError]:
    return sorted(
        validator.iter_errors(payload),
        key=lambda error: (tuple(str(part) for part in error.absolute_path), error.message),
    )


def _format_errors(errors: list[ValidationError]) -> str:
    return "\n".join(
        f"{'.'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in errors
    )


def _valid_v2_lane() -> dict[str, Any]:
    return {
        "schema_version": "bb.e4.lane_def.v2",
        "lane_id": "lane_alpha",
        "config_id": "config_alpha",
        "target_family": "claude_code",
        "target_version": "claude-code 2.1.63",
        "package_ref": "config/e4_targets/claude_code/2.1.63",
        "kind": "target_support",
        "status": "accepted",
        "points": 100,
        "run": {
            "run_id": "run_alpha",
            "provider_model": "anthropic/claude-haiku-4-5-20251001",
            "sandbox_mode": "read-only static package capture",
        },
        "provenance": {
            "upstream_repo": "https://github.com/anthropics/claude-code.git",
            "upstream_commit": "9582ad480f687bbeaf0025852ac4f020b07f20bb",
            "upstream_commit_date": "2026-03-05T00:25:31Z",
            "upstream_release_label": "claude-code@snapshot-2026-03-04",
            "source_paths": ["agent_configs/claude_code_2-1-63_e4_3-6-2026.yaml"],
        },
        "acceptance": {
            "behavior_family": "replay_capture",
            "semantic_key": "north_star_claude_code_package_capture",
            "target": "claude",
            "assertions": [
                {"id": "target_config_present", "description": "target config is present"},
                {"id": "pinned_target_version", "description": "target version is pinned"},
            ],
        },
        "capture": {
            "strategy": "replay_dump",
            "argv": None,
            "inputs": ["config/e4_targets/claude_code/2.1.63"],
            "workspace_template": None,
        },
        "normalize": {"translator": "identity", "config": {}},
        "replay": {"session": None, "comparator_class": "semantic"},
        "compare": {
            "comparator": "north_star_stored_report_replay",
            "config": {
                "assertions": [
                    {
                        "id": "ok_true",
                        "path": "ok",
                        "op": "equals",
                        "value": True,
                        "description": "replay report is ok",
                    }
                ]
            },
        },
        "claim": {
            "scope": {"behaviors": ["bb.replay_session.v1"], "surfaces": ["exact-scope package capture"]},
            "exclusions": ["broad target-family support"],
        },
        "ct": {"description": "C4 exact-scope lane", "timeout_seconds": 60, "test_id": "CT-LANE-ALPHA"},
        "artifacts_root": "docs/conformance/e4_target_support/lane_alpha",
        "reverify_command": {"argv": ["python", "scripts/validate_e4_c4_chain.py", "--check-only"], "cwd": "."},
        "metadata": {"note": "non-normative"},
    }


def _write_yaml(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")


def _v1_lane() -> dict[str, Any]:
    return {
        "schema_version": "bb.e4.lane_def.v1",
        "lane_id": "lane_alpha",
        "config_id": "config_alpha",
        "target_family": "claude_code",
        "target_version": "claude-code 2.1.63",
        "package_ref": "config/e4_targets/claude_code/2.1.63",
        "kind": "target_support",
        "status": "accepted",
        "points": 100,
        "capture": {"strategy": "replay_dump", "argv": None, "inputs": ["config/e4_targets/claude_code/2.1.63"], "workspace_template": None},
        "normalize": {"translator": "identity", "config": {}},
        "replay": {"session": None, "comparator_class": "semantic"},
        "compare": {"comparator": "north_star_stored_report_replay", "config": {"assertions": [{"path": "ok", "equals": True}]}},
        "claim": {
            "scope": {"behaviors": ["bb.replay_session.v1"], "surfaces": ["exact-scope package capture"]},
            "exclusions": ["broad target-family support"],
        },
        "ct": {"description": "legacy C4 exact-scope lane", "timeout_seconds": 60},
        "artifacts_root": "docs/conformance/e4_target_support/lane_alpha",
        "reverify_command": {"argv": ["python", "scripts/validate_e4_c4_chain.py", "--check-only"], "cwd": "."},
        "metadata": {
            "run_id": "run_alpha",
            "provider_model": "anthropic/claude-haiku-4-5-20251001",
            "sandbox_mode": "read-only static package capture",
            "legacy_inventory_ct_test_id": "CT-LANE-ALPHA",
            "acceptance_packet": {
                "behavior_family": "replay_capture",
                "semantic_key": "north_star_claude_code_package_capture",
                "target": "claude",
                "upstream_repo": "https://github.com/anthropics/claude-code.git",
                "upstream_commit": "9582ad480f687bbeaf0025852ac4f020b07f20bb",
                "upstream_commit_date": "2026-03-05T00:25:31Z",
                "upstream_release_label": "claude-code@snapshot-2026-03-04",
                "source_paths": ["agent_configs/claude_code_2-1-63_e4_3-6-2026.yaml"],
                "assertions": [{"id": "target_config_present", "description": "target config is present"}],
            },
        },
    }


def test_v2_lane_def_validates_through_loader(tmp_path: Path, lane_def_v2_validator: Draft202012Validator) -> None:
    lane = _valid_v2_lane()
    assert _schema_errors(lane_def_v2_validator, lane) == []
    lane_path = tmp_path / "lane_alpha.yaml"
    _write_yaml(lane_path, lane)

    loaded = load_lane_def(lane_path)

    assert loaded["schema_version"] == "bb.e4.lane_def.v2"
    assert loaded["acceptance"]["assertions"][0]["id"] == "target_config_present"


@pytest.mark.parametrize("status", ["accepted", "claimed"])
@pytest.mark.parametrize("missing_field", ["run", "acceptance", "provenance"])
def test_accepted_and_claimed_lane_defs_require_run_acceptance_and_provenance(
    lane_def_v2_validator: Draft202012Validator,
    status: str,
    missing_field: str,
) -> None:
    lane = _valid_v2_lane()
    lane["status"] = status
    lane.pop(missing_field)

    errors = _schema_errors(lane_def_v2_validator, lane)

    assert errors != []
    assert missing_field in _format_errors(errors)


def test_adapter_capture_strategy_requires_adapter_id(lane_def_v2_validator: Draft202012Validator) -> None:
    lane = _valid_v2_lane()
    lane["capture"] = {
        "strategy": "adapter",
        "argv": None,
        "inputs": ["docs_tmp/phase_15/packet"],
        "workspace_template": None,
    }

    missing_adapter_errors = _schema_errors(lane_def_v2_validator, lane)

    assert missing_adapter_errors != []
    assert "adapter" in _format_errors(missing_adapter_errors)

    lane["capture"]["adapter"] = "north_star_package_capture"
    assert _schema_errors(lane_def_v2_validator, lane) == []


def test_v1_lane_loads_normalized_compatibility_views(tmp_path: Path) -> None:
    lane_path = tmp_path / "lane_alpha.yaml"
    _write_yaml(lane_path, _v1_lane())

    loaded = load_lane_def(lane_path)

    assert loaded["_lane_def_version"] == 1
    assert loaded["run"] == {
        "run_id": "run_alpha",
        "provider_model": "anthropic/claude-haiku-4-5-20251001",
        "sandbox_mode": "read-only static package capture",
    }
    assert loaded["provenance"]["upstream_commit"] == "9582ad480f687bbeaf0025852ac4f020b07f20bb"
    assert loaded["acceptance"] == {
        "behavior_family": "replay_capture",
        "semantic_key": "north_star_claude_code_package_capture",
        "target": "claude",
        "assertions": [{"id": "target_config_present", "description": "target config is present"}],
    }
    assert loaded["ct"]["test_id"] == "CT-LANE-ALPHA"


def test_unknown_lane_def_schema_version_errors_clearly(tmp_path: Path) -> None:
    lane = copy.deepcopy(_v1_lane())
    lane["schema_version"] = "bb.e4.lane_def.v404"
    lane_path = tmp_path / "unknown_lane.yaml"
    _write_yaml(lane_path, lane)

    with pytest.raises(LaneDefValidationError) as exc_info:
        load_lane_def(lane_path)

    message = str(exc_info.value)
    assert "unknown schema_version" in message
    assert "bb.e4.lane_def.v404" in message
    assert "bb.e4.lane_def.v1" in message and "bb.e4.lane_def.v2" in message
