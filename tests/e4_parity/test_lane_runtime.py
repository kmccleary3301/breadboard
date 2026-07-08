from __future__ import annotations

import hashlib
import json
from pathlib import Path

from scripts.e4_parity import lane_runtime
from scripts.e4_parity import build_oh_my_pi_p3_1_effective_config_graph as p3_1
from scripts.e4_parity import build_oh_my_pi_p3_remaining_helper_runtime as p3_remaining


def test_canonical_json_supports_default_and_compact_styles() -> None:
    payload = {"z": 1, "a": [2, {"b": "c"}]}

    assert lane_runtime.canonical_json(payload) == '{\n  "a": [\n    2,\n    {\n      "b": "c"\n    }\n  ],\n  "z": 1\n}\n'
    assert lane_runtime.canonical_json(payload, separators_style="compact") == '{"a":[2,{"b":"c"}],"z":1}'


def test_prefixed_hashing_for_text_and_files(tmp_path: Path) -> None:
    path = tmp_path / "artifact.txt"
    path.write_text("hello\n", encoding="utf-8")
    expected = "sha256:" + hashlib.sha256(b"hello\n").hexdigest()

    assert lane_runtime.sha256_text("hello\n") == expected
    assert lane_runtime.sha256_file(path) == expected


def test_generated_at_utc_uses_env_before_required_default(monkeypatch) -> None:
    monkeypatch.delenv("BB_E4_GENERATED_AT_UTC", raising=False)
    assert lane_runtime.generated_at_utc(default="2026-07-03T07:30:00Z") == "2026-07-03T07:30:00Z"

    monkeypatch.setenv("BB_E4_GENERATED_AT_UTC", "2030-01-02T03:04:05Z")
    assert lane_runtime.generated_at_utc(default="2026-07-03T07:30:00Z") == "2030-01-02T03:04:05Z"


def test_display_path_matches_lane_builder_repo_and_workspace_semantics(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_file = repo_root / "docs" / "claim.json"
    workspace_file = tmp_path / "docs_tmp" / "phase_15" / "ledger.json"
    outside_file = tmp_path.parent / "outside-lane-runtime.txt"
    repo_file.parent.mkdir(parents=True)
    workspace_file.parent.mkdir(parents=True)
    repo_file.write_text("{}", encoding="utf-8")
    workspace_file.write_text("{}", encoding="utf-8")
    outside_file.write_text("x", encoding="utf-8")

    try:
        assert lane_runtime.display_path(repo_file, repo_root=repo_root) == "docs/claim.json"
        assert lane_runtime.display_path(workspace_file, repo_root=repo_root) == "docs_tmp/phase_15/ledger.json"
        assert lane_runtime.display_path(outside_file, repo_root=repo_root) == outside_file.resolve().as_posix()
    finally:
        outside_file.unlink(missing_ok=True)


def test_write_json_artifact_styles_and_returned_sha(tmp_path: Path) -> None:
    default_path = tmp_path / "default.json"
    compact_path = tmp_path / "compact.json"
    payload = {"b": 2, "a": 1}

    default_sha = lane_runtime.write_json_artifact(default_path, payload, style="default", trailing_newline=True)
    compact_sha = lane_runtime.write_json_artifact(compact_path, payload, style="compact", trailing_newline=False)

    assert default_path.read_text(encoding="utf-8") == '{\n  "a": 1,\n  "b": 2\n}\n'
    assert compact_path.read_text(encoding="utf-8") == '{"a":1,"b":2}'
    assert default_sha == lane_runtime.sha256_text(default_path.read_text(encoding="utf-8"))
    assert compact_sha == lane_runtime.sha256_text(compact_path.read_text(encoding="utf-8"))


def test_upsert_ledger_row_replaces_feature_and_returns_compact_row_hash(tmp_path: Path) -> None:
    ledger_path = tmp_path / "ledger.json"
    original = {
        "rows": [
            {"feature_id": "keep", "value": 1},
            {"feature_id": "replace", "value": 2},
        ],
        "row_count": 2,
    }
    ledger_path.write_text(json.dumps(original), encoding="utf-8")
    row = {"feature_id": "replace", "value": 3}

    row_ref = lane_runtime.upsert_ledger_row(ledger_path, row, feature_id="replace")

    updated = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert updated["rows"] == [{"feature_id": "keep", "value": 1}, row]
    assert updated["row_count"] == 2
    expected_row_text = lane_runtime.canonical_json({"row_id": "replace", "row": row}, separators_style="compact")
    assert row_ref == lane_runtime.sha256_text(expected_row_text)


def test_migrated_builder_module_constants_still_exist() -> None:
    assert p3_1.GENERATED_AT_UTC == "2026-07-03T07:30:00Z"
    assert isinstance(p3_1.CONFIG_ID, str) and p3_1.CONFIG_ID
    assert isinstance(p3_1.CLAIM_ID, str) and p3_1.CLAIM_ID
    assert isinstance(p3_1.CT_OUTPUT, str) and p3_1.CT_OUTPUT

    assert p3_remaining.GENERATED_AT_UTC == "2026-07-03T07:30:00Z"
    assert isinstance(p3_remaining.LANES, tuple) and p3_remaining.LANES
    for spec in p3_remaining.LANES:
        assert isinstance(spec["config_id"], str) and spec["config_id"]
        assert isinstance(spec["ct_output"], str) and spec["ct_output"]
