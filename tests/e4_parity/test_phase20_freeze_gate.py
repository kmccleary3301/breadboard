from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from scripts import check_phase20_freeze


SHORT_SCHEMA_ID = "bb.e4.lane_lock.v1"
URL_SCHEMA_ID = (
    "https://breadboard.dev/contracts/kernel/schemas/bb.agent_config_surface.v1.schema.json"
)
TEST_BASELINE_SHA = "1" * 40


@dataclass(frozen=True)
class FreezeRepository:
    schemas: dict[str, Path]

    def replace_schema(self, schema_id: str) -> str:
        path = self.schemas[schema_id]
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["description"] = "tightened after the freeze"
        content = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
        path.write_bytes(content)
        return hashlib.sha256(content).hexdigest()


def _write_json(path: Path, payload: dict[str, Any]) -> bytes:
    content = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return content


@pytest.fixture
def freeze_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> FreezeRepository:
    schema_root = tmp_path / "contracts/kernel/schemas"
    lock_path = schema_root / "bb.e4.lane_lock.v1.schema.json"
    url_schema_path = schema_root / "bb.agent_config_surface.v1.schema.json"
    schema_contents = {
        SHORT_SCHEMA_ID: _write_json(
            lock_path,
            {
                "$id": SHORT_SCHEMA_ID,
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "properties": {"target_freeze": {"type": "object"}},
                "type": "object",
            },
        ),
        URL_SCHEMA_ID: _write_json(
            url_schema_path,
            {
                "$id": URL_SCHEMA_ID,
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "properties": {"agent": {"type": "string"}},
                "type": "object",
            },
        ),
    }
    baseline_path = (
        tmp_path
        / "docs/plans/phase_20_right_shape/PHASE20_FREEZE_BASELINE.json"
    )
    _write_json(
        baseline_path,
        {
            "baseline_sha": TEST_BASELINE_SHA,
            "inventory": {
                "lane_ids": [],
                "lane_kinds": [],
                "schema_content_sha256": {
                    schema_id: hashlib.sha256(content).hexdigest()
                    for schema_id, content in schema_contents.items()
                },
                "schema_ids": sorted(schema_contents),
                "sdk_packages": [],
                "top_level_governance_files": [],
            },
        },
    )

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)

    monkeypatch.setattr(check_phase20_freeze, "ROOT", tmp_path)
    monkeypatch.setattr(check_phase20_freeze, "BASELINE_PATH", baseline_path)
    monkeypatch.setattr(check_phase20_freeze, "BASELINE_SHA", TEST_BASELINE_SHA)
    monkeypatch.setattr(check_phase20_freeze, "SCHEMA_ROOTS", (schema_root,))
    monkeypatch.setattr(
        check_phase20_freeze,
        "SDK_ROOTS",
        (tmp_path / "sdk", tmp_path / "breadboard_sdk"),
    )
    monkeypatch.setattr(check_phase20_freeze, "LANE_ROOT", tmp_path / "config/e4_lanes")
    monkeypatch.setattr(check_phase20_freeze, "LANE_LOCK_SCHEMA_PATH", lock_path)
    monkeypatch.setattr(check_phase20_freeze, "TIGHTENING_ALLOWLIST", {})
    monkeypatch.setattr(
        check_phase20_freeze,
        "validate_contract_tiers",
        lambda *, tracked_files: [],
    )

    return FreezeRepository(
        schemas={
            SHORT_SCHEMA_ID: lock_path,
            URL_SCHEMA_ID: url_schema_path,
        }
    )


def test_clean_tracked_inventory_passes_freeze_gate(
    freeze_repository: FreezeRepository, capsys: pytest.CaptureFixture[str]
) -> None:
    assert check_phase20_freeze.main() == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == f"phase20-freeze: PASS (baseline {TEST_BASELINE_SHA})\n"


@pytest.mark.parametrize("schema_id", [SHORT_SCHEMA_ID, URL_SCHEMA_ID])
def test_unallowlisted_schema_drift_reports_paste_ready_remedy_for_verbatim_id(
    freeze_repository: FreezeRepository,
    capsys: pytest.CaptureFixture[str],
    schema_id: str,
) -> None:
    post_change_sha = freeze_repository.replace_schema(schema_id)

    assert check_phase20_freeze.main() == 1

    captured = capsys.readouterr()
    remedy = json.dumps(
        {
            schema_id: {
                "packet": "<packet-id>",
                "sha256": post_change_sha,
            }
        },
        sort_keys=True,
    )
    assert captured.out == ""
    assert "phase20-freeze: frozen semantic surface violations detected" in captured.err
    assert f"schema_content_drift: {schema_id}" in captured.err
    assert f"add/update {remedy} per FREEZE_POLICY/AM10 in the same commit" in captured.err
    assert "or revert the change" in captured.err


def test_allowlist_with_wrong_post_change_hash_does_not_authorize_drift(
    freeze_repository: FreezeRepository,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    freeze_repository.replace_schema(SHORT_SCHEMA_ID)
    monkeypatch.setattr(
        check_phase20_freeze,
        "TIGHTENING_ALLOWLIST",
        {SHORT_SCHEMA_ID: {"packet": "F1", "sha256": "0" * 64}},
    )

    assert check_phase20_freeze.main() == 1

    captured = capsys.readouterr()
    assert f"schema_content_drift: {SHORT_SCHEMA_ID}" in captured.err
    assert "not authorized by TIGHTENING_ALLOWLIST" in captured.err


def test_allowlist_with_exact_post_change_hash_authorizes_drift(
    freeze_repository: FreezeRepository,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    post_change_sha = freeze_repository.replace_schema(SHORT_SCHEMA_ID)
    monkeypatch.setattr(
        check_phase20_freeze,
        "TIGHTENING_ALLOWLIST",
        {SHORT_SCHEMA_ID: {"packet": "F1", "sha256": post_change_sha}},
    )

    assert check_phase20_freeze.main() == 0

    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == f"phase20-freeze: PASS (baseline {TEST_BASELINE_SHA})\n"


def test_bare_allowlist_hash_is_a_configuration_error(
    freeze_repository: FreezeRepository,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    freeze_repository.replace_schema(SHORT_SCHEMA_ID)
    monkeypatch.setattr(
        check_phase20_freeze,
        "TIGHTENING_ALLOWLIST",
        {SHORT_SCHEMA_ID: "0" * 64},
    )

    assert check_phase20_freeze.main() == 2

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "phase20-freeze: allowlist config error:" in captured.err
    assert f"{SHORT_SCHEMA_ID}: expected exactly packet and sha256 fields" in captured.err
