from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

import pytest
import yaml
from jsonschema import Draft202012Validator

from scripts.authoring import validate_lane


ROOT = Path(__file__).resolve().parents[2]
MANIFEST_SCHEMA_PATH = ROOT / "contracts" / "kernel" / "schemas" / "bb.e4.lane_manifest.v1.schema.json"


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def _minimal_manifest() -> dict[str, Any]:
    return {
        "schema_version": "bb.e4.lane_manifest.v1",
        "lane_id": "fixture_lane",
        "config_id": "fixture.config",
        "target": {"family": "fixture", "version": "1"},
        "kind": "probe",
        "capture": {"strategy": "probe_argv", "argv": ["python", "probe.py"], "inputs": []},
        "normalize": {"mode": "identity"},
        "replay": {"mode": "stored", "comparator_class": "exact"},
        "compare": {
            "comparator": "exact",
            "assertions": [
                {
                    "assertion_id": "fixture-output",
                    "kind": "json_equal",
                    "description": "fixture output is stable",
                }
            ],
        },
        "claim": {"scope": {"behaviors": ["fixture behavior"]}, "exclusions": []},
        "reverify_command": {"argv": ["python", "verify.py"], "cwd": "."},
        "artifacts_root": "artifacts/fixture",
        "notes": "Fixture manifest without resolved digests.",
    }


ManifestMutation = Callable[[dict[str, Any]], None]


def _set_notes(manifest: dict[str, Any]) -> None:
    manifest["notes"] = "resolved as sha256:deadbeef"


def _set_assertion_description(manifest: dict[str, Any]) -> None:
    manifest["compare"]["assertions"][0]["description"] = "matches sha256:deadbeef"


def _set_capture_argv(manifest: dict[str, Any]) -> None:
    manifest["capture"]["argv"][1] = "sha256:deadbeef"


def _set_capture_inputs(manifest: dict[str, Any]) -> None:
    manifest["capture"]["inputs"] = ["fixtures/sha256:deadbeef.json"]


def _set_reverify_cwd(manifest: dict[str, Any]) -> None:
    manifest["reverify_command"]["cwd"] = "scratch/sha256:deadbeef"


def _set_nested_projection_constant(manifest: dict[str, Any]) -> None:
    manifest["normalize"]["projection_constants"] = {
        "arbitrary": {"nested": ["still forbidden: sha256:deadbeef"]}
    }


@pytest.mark.parametrize(
    "mutation",
    [
        pytest.param(_set_notes, id="notes"),
        pytest.param(_set_assertion_description, id="compare-assertion-description"),
        pytest.param(_set_capture_argv, id="capture-argv"),
        pytest.param(_set_capture_inputs, id="capture-inputs"),
        pytest.param(_set_reverify_cwd, id="reverify-cwd"),
        pytest.param(_set_nested_projection_constant, id="nested-arbitrary-location"),
    ],
)
def test_manifest_loader_rejects_digest_substring_recursively(
    tmp_path: Path,
    mutation: ManifestMutation,
) -> None:
    """A manifest cannot smuggle a resolved digest through any author-controlled string."""
    manifest = _minimal_manifest()
    mutation(manifest)
    manifest_path = tmp_path / "lane.manifest.yaml"
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match=r"sha256:"):
        validate_lane.load_lane_manifest(manifest_path)


def test_validate_lane_rejects_digest_before_schema_validation(tmp_path: Path) -> None:
    """The public validator reports forbidden digests before an invalid schema version."""
    manifest_path = tmp_path / "untracked-lane.yaml"
    manifest_path.write_text(
        "schema_version: bb.e4.lane_manifest.invalid\n"
        "metadata:\n"
        "  provenance:\n"
        "    digest: sha256:deadbeef\n",
        encoding="utf-8",
    )

    report = validate_lane.validate_lane(str(manifest_path))

    schema_check = next(
        check for check in report["checks"] if check["check_id"] == "lane_def_schema_valid"
    )
    assert schema_check["status"] == "failed"
    detail = schema_check["detail"]
    assert "forbidden" in detail
    assert "sha256:" in detail
    assert "unknown schema_version" not in detail
    assert "schema validation" not in detail.lower()


def test_manifest_schema_is_draft_2020_12_and_accepts_minimal_manifest() -> None:
    """The published manifest schema is itself valid and accepts the smallest complete intent document."""
    schema = _load_json(MANIFEST_SCHEMA_PATH)
    Draft202012Validator.check_schema(schema)

    Draft202012Validator(schema).validate(_minimal_manifest())
