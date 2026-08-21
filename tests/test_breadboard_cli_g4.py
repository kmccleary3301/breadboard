from __future__ import annotations

import json
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml

from scripts import breadboard_cli
from scripts.e4_parity import run_lane


LANE_ID = "g4_cli_fixture"
CONFIG_ID = "g4.cli.fixture"
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def compiler_input_dir() -> Iterator[Path]:
    path = Path(
        tempfile.mkdtemp(prefix=".g4-cli-", dir=ROOT / "scripts/e4_parity/adapters")
    )
    try:
        yield path
    finally:
        shutil.rmtree(path)


def _write_manifest(tmp_path: Path, compiler_input_dir: Path) -> Path:
    freeze_path = compiler_input_dir / "target-freeze.yaml"
    freeze_path.write_text(
        yaml.safe_dump(
            {
                "e4_configs": {
                    CONFIG_ID: {
                        "family": "fixture",
                        "version": "1.0",
                        "source": "declared-test-input",
                        "harness": {
                            "upstream_repo": "https://example.invalid/fixture",
                            "upstream_commit": "0" * 40,
                            "upstream_commit_date": "2026-01-01T00:00:00Z",
                            "upstream_release_label": "fixture-1.0",
                        },
                    }
                }
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    constants_path = compiler_input_dir / "capture-input.json"
    constants_path.write_text(
        json.dumps(
            {
                "packet_constants": {
                    "payload_templates": {
                        "request": {"prompt": "fixture"},
                        "target_probe_output": {
                            "run_id": "fixture-run",
                            "provider_model": "fixture-model",
                            "sandbox_mode": "read-only",
                        },
                    },
                    "substitutions": {"MODEL": "fixture-model"},
                }
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    manifest = {
        "schema_version": "bb.e4.lane_manifest.v1",
        "lane_id": LANE_ID,
        "config_id": CONFIG_ID,
        "target": {
            "family": "fixture",
            "version": "1.0",
            "source_freeze_ref": freeze_path.relative_to(ROOT).as_posix(),
        },
        "kind": "target_support",
        "capture": {
            "strategy": "probe_argv",
            "argv": ["python", "probe.py"],
            "inputs": [constants_path.relative_to(ROOT).as_posix()],
        },
        "normalize": {"mode": "identity", "translator": "identity"},
        "replay": {"mode": "stored", "comparator_class": "semantic"},
        "compare": {"comparator": "semantic_replay_v1"},
        "claim": {
            "scope": {
                "behaviors": ["fixture behavior"],
                "surfaces": ["G4 CLI capture"],
            },
            "exclusions": [],
        },
        "acceptance": {
            "behavior_family": "fixture",
            "semantic_key": "g4_cli_fixture",
            "target": "fixture",
            "assertions": [
                {"id": "capture_completed", "description": "capture completed"}
            ],
        },
        "reverify_command": {"argv": ["python", "verify.py"], "cwd": "."},
        "artifacts_root": "artifacts/g4_cli_fixture",
    }
    manifest_path = tmp_path / "lane.manifest.yaml"
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False),
        encoding="utf-8",
    )
    return manifest_path


def test_lane_lock_writes_to_out_and_check_reports_artifact_drift(
    tmp_path: Path,
    compiler_input_dir: Path,
) -> None:
    manifest_path = _write_manifest(tmp_path, compiler_input_dir)
    out_dir = tmp_path / "compiled"
    lock_path = out_dir / f"{LANE_ID}.lock.json"
    sidecar_path = out_dir / f"{LANE_ID}.packet_constants.v1.json"
    command = ["lane", "lock", str(manifest_path), "--out", str(out_dir)]

    assert breadboard_cli.main(command) == 0
    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    assert lock["lane_id"] == LANE_ID
    assert lock["packet_constants_ref"]["path"] == str(sidecar_path)
    assert sidecar == {
        "payload_templates": {
            "request": {"prompt": "fixture"},
            "target_probe_output": {
                "run_id": "fixture-run",
                "provider_model": "fixture-model",
                "sandbox_mode": "read-only",
            },
        },
        "substitutions": {"MODEL": "fixture-model"},
    }

    assert breadboard_cli.main([*command, "--check"]) == 0

    tampered = b'{"tampered":true}\n'
    sidecar_path.write_bytes(tampered)
    assert breadboard_cli.main([*command, "--check"]) == 5
    assert sidecar_path.read_bytes() == tampered


def test_lane_capture_delegates_exact_manifest_directory_to_capture_stage(
    tmp_path: Path,
    monkeypatch,
    compiler_input_dir: Path,
) -> None:
    supplied_manifest_dir = tmp_path / "supplied-lanes"
    supplied_manifest_dir.mkdir()
    manifest_path = _write_manifest(supplied_manifest_dir, compiler_input_dir)
    out_dir = tmp_path / "capture"
    received: list[list[str]] = []

    def fake_run_lane_main(argv: list[str]) -> int:
        received.append(argv)
        return 17

    monkeypatch.setattr(run_lane, "main", fake_run_lane_main)

    exit_code = breadboard_cli.main(
        ["lane", "capture", str(manifest_path), "--out", str(out_dir)]
    )

    assert exit_code == 17
    assert received == [
        [
            "--lane",
            LANE_ID,
            "--stage",
            "capture",
            "--out",
            str(out_dir),
            "--lane-def-dir",
            str(supplied_manifest_dir),
        ]
    ]


def test_lane_capture_runs_supplied_manifest_when_default_has_same_lane_id(
    tmp_path: Path,
    monkeypatch,
    compiler_input_dir: Path,
) -> None:
    supplied_manifest_dir = compiler_input_dir / "supplied-lanes"
    default_manifest_dir = compiler_input_dir / "default-lanes"
    supplied_manifest_dir.mkdir()
    default_manifest_dir.mkdir()
    supplied_manifest = _write_manifest(supplied_manifest_dir, compiler_input_dir)
    default_manifest = _write_manifest(default_manifest_dir, compiler_input_dir)

    supplied_data = yaml.safe_load(supplied_manifest.read_text(encoding="utf-8"))
    supplied_data["capture"] = {
        "strategy": "replay_dump",
        "argv": None,
        "inputs": supplied_data["capture"]["inputs"],
    }
    supplied_manifest.write_text(
        yaml.safe_dump(supplied_data, sort_keys=False),
        encoding="utf-8",
    )

    default_data = yaml.safe_load(default_manifest.read_text(encoding="utf-8"))
    default_data["capture"] = {
        "strategy": "probe_argv",
        "argv": ["python", "-c", "raise SystemExit(9)"],
        "inputs": [],
    }
    default_manifest.write_text(
        yaml.safe_dump(default_data, sort_keys=False),
        encoding="utf-8",
    )
    assert breadboard_cli.main(["lane", "lock", str(supplied_manifest)]) == 0
    assert breadboard_cli.main(["lane", "lock", str(default_manifest)]) == 0
    supplied_lock_path = supplied_manifest.with_name(f"{LANE_ID}.lock.json")
    supplied_lock = json.loads(supplied_lock_path.read_text(encoding="utf-8"))
    supplied_lock["artifact_roles"] = {
        "replay_ref": {"path": "pyproject.toml", "sha256": None, "bytes": None}
    }
    supplied_lock_path.write_text(
        json.dumps(supplied_lock, indent=2) + "\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(run_lane, "DEFAULT_LANE_DEF_DIR", default_manifest_dir)

    assert breadboard_cli.main(
        [
            "lane",
            "capture",
            str(supplied_manifest),
            "--out",
            str(tmp_path / "capture"),
        ]
    ) == 0


@pytest.mark.parametrize("command", ["lock", "capture"])
def test_lane_command_missing_manifest_returns_validation_exit(
    tmp_path: Path,
    monkeypatch,
    command: str,
) -> None:
    def fail_if_delegated(argv: list[str]) -> int:
        raise AssertionError(f"runner must not receive a missing manifest: {argv}")

    monkeypatch.setattr(run_lane, "main", fail_if_delegated)

    assert breadboard_cli.main(
        ["lane", command, str(tmp_path / "missing.manifest.yaml")]
    ) == 3
