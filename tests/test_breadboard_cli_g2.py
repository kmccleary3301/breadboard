from __future__ import annotations

import json
from pathlib import Path

import yaml

from scripts import breadboard_cli
from scripts.authoring.validate_lane import load_lane_manifest


def _invoke(argv: list[str], capsys) -> tuple[int, str, str]:
    exit_code = breadboard_cli.main(argv)
    captured = capsys.readouterr()
    return exit_code, captured.out, captured.err


def test_harness_init_produces_a_valid_explainable_bundle_without_overwriting(
    tmp_path: Path,
    capsys,
) -> None:
    out_dir = tmp_path / "harness"

    exit_code, _, stderr = _invoke(["harness", "init", "--out", str(out_dir)], capsys)

    assert exit_code == 0, stderr
    harness_path = out_dir / "minimal_harness.v2.yaml"
    prompt_path = out_dir / "prompts" / "minimal_system.md"
    assert harness_path.is_file()
    assert prompt_path.is_file()

    exit_code, _, stderr = _invoke(
        ["harness", "validate", str(harness_path)], capsys
    )
    assert exit_code == 0, stderr

    exit_code, stdout, stderr = _invoke(
        ["harness", "explain", str(harness_path)], capsys
    )
    assert exit_code == 0, stderr
    explanation = json.loads(stdout)
    assert explanation["schema_version"] == "bb.config_explanation.v1"
    assert explanation["surface_schema_version"] == "bb.agent_config_surface.v2"
    assert explanation["ok"] is True
    assert explanation["diagnostics"] == []

    harness_path.write_text("author-owned harness\n", encoding="utf-8")
    prompt_path.write_text("author-owned prompt\n", encoding="utf-8")
    before = {
        harness_path: harness_path.read_bytes(),
        prompt_path: prompt_path.read_bytes(),
    }

    exit_code, _, stderr = _invoke(["harness", "init", "--out", str(out_dir)], capsys)

    assert exit_code == 2
    assert "exist" in stderr.lower() or "overwrite" in stderr.lower()
    assert {path: path.read_bytes() for path in before} == before


def test_lane_init_produces_a_loader_valid_manifest_without_overwriting(
    tmp_path: Path,
    capsys,
) -> None:
    out_dir = tmp_path / "lane"

    exit_code, _, stderr = _invoke(["lane", "init", "--out", str(out_dir)], capsys)

    assert exit_code == 0, stderr
    manifest_path = out_dir / "lane.manifest.yaml"
    loaded = load_lane_manifest(manifest_path)
    assert loaded["schema_version"] == "bb.e4.lane_manifest.v1"

    exit_code, _, stderr = _invoke(["lane", "validate", str(manifest_path)], capsys)
    assert exit_code == 0, stderr

    manifest_path.write_text("author-owned lane\n", encoding="utf-8")
    before = manifest_path.read_bytes()

    exit_code, _, stderr = _invoke(["lane", "init", "--out", str(out_dir)], capsys)

    assert exit_code == 2
    assert "exist" in stderr.lower() or "overwrite" in stderr.lower()
    assert manifest_path.read_bytes() == before


def test_harness_validate_returns_pointerful_schema_failure(
    tmp_path: Path,
    capsys,
) -> None:
    harness_path = tmp_path / "invalid-harness.yaml"
    harness_path.write_text(
        """schema_version: bb.agent_config_surface.v2
version: 2
workspace:
  root: .
providers:
  default_model: broken
  models:
    - id: broken
modes:
  - name: main
loop:
  sequence:
    - mode: main
""",
        encoding="utf-8",
    )

    exit_code, _, stderr = _invoke(
        ["harness", "validate", str(harness_path)], capsys
    )

    assert exit_code == 2
    assert "/providers/models/0/adapter" in stderr
    assert "required" in stderr.lower()


def test_lane_validate_returns_pointerful_schema_failure(
    tmp_path: Path,
    capsys,
) -> None:
    out_dir = tmp_path / "lane"
    exit_code, _, stderr = _invoke(["lane", "init", "--out", str(out_dir)], capsys)
    assert exit_code == 0, stderr
    manifest_path = out_dir / "lane.manifest.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["target"] = []
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")

    exit_code, _, stderr = _invoke(["lane", "validate", str(manifest_path)], capsys)

    assert exit_code == 2
    assert "/target" in stderr
    assert "object" in stderr.lower()
