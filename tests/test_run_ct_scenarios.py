from __future__ import annotations

import json
from pathlib import Path
import sys

import scripts.run_ct_scenarios as ct_runner


def _write_helper(path: Path, payload: dict[str, object], *, sleep_seconds: float = 0.0) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "from __future__ import annotations\n"
        "import argparse, json, time\n"
        "from pathlib import Path\n"
        "parser = argparse.ArgumentParser()\n"
        "parser.add_argument('--json-out', required=True)\n"
        "args = parser.parse_args()\n"
        f"time.sleep({sleep_seconds!r})\n"
        f"payload = {payload!r}\n"
        "out = Path(args.json_out)\n"
        "out.parent.mkdir(parents=True, exist_ok=True)\n"
        "out.write_text(json.dumps(payload) + '\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )


def _write_manifest(
    path: Path,
    *,
    command: list[str],
    expected_ok: bool = True,
    test_id: str = "CT-UNIT-001",
    gate_level: str = "P0-blocking",
    artifact_path: str = "artifacts/conformance/node_gate/ct_unit_001.json",
) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "ct_scenarios_manifest_v1",
                "suite_id": "unit-suite",
                "target": {"kind": "unit", "id": "unit"},
                "scenarios": [
                    {
                        "test_id": test_id,
                        "description": "unit scenario",
                        "command": command,
                        "timeout_seconds": 30,
                        "assertions": {
                            "json_files": [
                                {
                                    "path": artifact_path,
                                    "checks": [
                                        {"path": "ok", "equals": expected_ok},
                                        {"path": "count", "min": 1},
                                        {"path": "violations", "length_equals": 0},
                                    ],
                                }
                            ]
                        },
                        "gate_level": gate_level,
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def test_run_ct_scenarios_executes_command_and_remaps_artifact_paths(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(ct_runner, "ROOT", repo_root)
    helper = repo_root / "scripts" / "write_report.py"
    _write_helper(helper, {"ok": True, "count": 3, "violations": []})
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        command=[
            "python",
            "scripts/write_report.py",
            "--json-out",
            "artifacts/conformance/node_gate/ct_unit_001.json",
        ],
    )
    result = tmp_path / "artifacts" / "ct_scenarios_result_v1.json"
    rows = tmp_path / "artifacts" / "ct_scenarios_rows_v1.json"

    exit_code = ct_runner.main(
        ["--manifest", str(manifest), "--json-out", str(result), "--rows-out", str(rows)]
    )

    assert exit_code == 0
    report = json.loads(result.read_text(encoding="utf-8"))
    row_payload = json.loads(rows.read_text(encoding="utf-8"))
    assert report["ok"] is True
    assert report["status"] == "pass"
    assert report["passing_count"] == 1
    assert row_payload[0]["status"] == "pass"
    assert (tmp_path / "artifacts" / "node_gate" / "ct_unit_001.json").is_file()


def test_run_ct_scenarios_zero_durations_sets_all_row_durations_to_zero(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(ct_runner, "ROOT", repo_root)
    helper = repo_root / "scripts" / "write_report.py"
    _write_helper(helper, {"ok": True, "count": 3, "violations": []})
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        command=[
            "python",
            "scripts/write_report.py",
            "--json-out",
            "artifacts/conformance/node_gate/ct_unit_001.json",
        ],
    )
    result = tmp_path / "ct_scenarios_result_v1.json"
    rows = tmp_path / "ct_scenarios_rows_v1.json"

    exit_code = ct_runner.main(
        [
            "--manifest",
            str(manifest),
            "--json-out",
            str(result),
            "--rows-out",
            str(rows),
            "--zero-durations",
        ]
    )

    assert exit_code == 0
    report = json.loads(result.read_text(encoding="utf-8"))
    row_payload = json.loads(rows.read_text(encoding="utf-8"))
    assert report["rows"][0]["duration_seconds"] == 0.0
    assert all(row["duration_seconds"] == 0.0 for row in row_payload)


def test_run_ct_scenarios_default_keeps_measured_duration_float(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(ct_runner, "ROOT", repo_root)
    helper = repo_root / "scripts" / "write_report.py"
    _write_helper(helper, {"ok": True, "count": 3, "violations": []}, sleep_seconds=0.02)
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        command=[
            "python",
            "scripts/write_report.py",
            "--json-out",
            "artifacts/conformance/node_gate/ct_unit_001.json",
        ],
    )
    result = tmp_path / "ct_scenarios_result_v1.json"
    rows = tmp_path / "ct_scenarios_rows_v1.json"

    exit_code = ct_runner.main(
        ["--manifest", str(manifest), "--json-out", str(result), "--rows-out", str(rows)]
    )

    assert exit_code == 0
    row_payload = json.loads(rows.read_text(encoding="utf-8"))
    duration = row_payload[0]["duration_seconds"]
    assert isinstance(duration, float)
    assert duration > 0.0


def test_regenerate_evidence_ct_scenarios_stage_uses_deterministic_duration_and_timestamp_flags() -> None:
    from scripts.e4_parity.regenerate_evidence import STAGES

    stage = next(stage for stage in STAGES if stage.stage_id == "ct_scenarios")
    argv = stage.argv
    assert "--zero-durations" in argv
    assert "--generated-at-utc" in argv

    assert argv[argv.index("--generated-at-utc") + 1] == "2026-07-03T00:00:00Z"


def test_regenerate_evidence_explain_expands_full_graph_without_workspace(
    monkeypatch, capsys
) -> None:
    from types import SimpleNamespace

    monkeypatch.delenv("BB_WORKSPACE_ROOT", raising=False)
    from scripts.e4_parity import regen as regen_front

    assert regen_front._explain(SimpleNamespace(json=None, python="python")) == 0
    explained = capsys.readouterr().out
    assert "01. source_index" in explained
    assert "49. validate_report_hash_freshness" in explained

    assert regen_front._explain(SimpleNamespace(json="-", python="python")) == 0
    plan = json.loads(capsys.readouterr().out)
    assert len(plan["stages"]) == len(regen_front.regen.STAGES)
    freshness = next(
        stage for stage in plan["stages"] if stage["stage_id"] == "validate_report_hash_freshness"
    )
    assert "{expected_points}" in freshness["argv"]
    assert "{expected_claims}" in freshness["argv"]

def test_run_ct_scenarios_marks_missing_blocking_command_not_implemented_by_default(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        command=[
            "python",
            "scripts/not_a_real_ct_checker.py",
            "--json-out",
            "artifacts/conformance/node_gate/ct_unit_001.json",
        ],
    )
    result = tmp_path / "result.json"
    rows = tmp_path / "rows.json"

    exit_code = ct_runner.main(
        ["--manifest", str(manifest), "--json-out", str(result), "--rows-out", str(rows)]
    )

    assert exit_code == 1
    report = json.loads(result.read_text(encoding="utf-8"))
    row_payload = json.loads(rows.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["status"] == "fail"
    assert report["planned_count"] == 0
    assert report["not_implemented_count"] == 1
    assert report["blocking_not_implemented_count"] == 1
    assert row_payload[0]["status"] == "not_implemented"
    assert "command script missing" in row_payload[0]["errors"][0]


def test_run_ct_scenarios_can_fail_on_missing_commands(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        command=[
            "python",
            "scripts/not_a_real_ct_checker.py",
            "--json-out",
            "artifacts/conformance/node_gate/ct_unit_001.json",
        ],
    )
    result = tmp_path / "result.json"
    rows = tmp_path / "rows.json"

    exit_code = ct_runner.main(
        [
            "--manifest",
            str(manifest),
            "--json-out",
            str(result),
            "--rows-out",
            str(rows),
            "--fail-on-unimplemented-all",
        ]
    )

    assert exit_code == 1
    report = json.loads(result.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["failing_count"] == 1


def test_run_ct_scenarios_fails_assertion_mismatches(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(ct_runner, "ROOT", repo_root)
    helper = repo_root / "scripts" / "write_report.py"
    _write_helper(helper, {"ok": False, "count": 3, "violations": []})
    manifest = tmp_path / "manifest.json"
    _write_manifest(
        manifest,
        command=[
            "python",
            "scripts/write_report.py",
            "--json-out",
            "artifacts/conformance/node_gate/ct_unit_001.json",
        ],
        expected_ok=True,
    )
    result = tmp_path / "result.json"
    rows = tmp_path / "rows.json"

    exit_code = ct_runner.main(
        ["--manifest", str(manifest), "--json-out", str(result), "--rows-out", str(rows)]
    )

    assert exit_code == 1
    report = json.loads(result.read_text(encoding="utf-8"))
    assert report["failing_count"] == 1
    assert "expected True" in report["rows"][0]["failures"][0]



def test_run_ct_scenarios_rejects_manifest_without_scenarios_list(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "ct_scenarios_manifest_v1",
                "suite_id": "unit-suite",
                "target": {"kind": "unit", "id": "unit"},
                "scenarios": {"test_id": "CT-UNIT-001"},
            }
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = ct_runner.main(
        [
            "--manifest",
            str(manifest),
            "--json-out",
            str(tmp_path / "result.json"),
            "--rows-out",
            str(tmp_path / "rows.json"),
        ]
    )

    assert exit_code == 2
    assert not (tmp_path / "result.json").exists()
    assert not (tmp_path / "rows.json").exists()


def test_run_ct_scenarios_rejects_duplicate_selected_test_ids(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.json"
    scenario = {
        "test_id": "CT-UNIT-DUP",
        "description": "duplicate scenario",
        "command": ["python", "scripts/not_a_real_ct_checker.py"],
        "gate_level": "P0-blocking",
    }
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "ct_scenarios_manifest_v1",
                "suite_id": "unit-suite",
                "target": {"kind": "unit", "id": "unit"},
                "scenarios": [scenario, dict(scenario)],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    exit_code = ct_runner.main(
        [
            "--manifest",
            str(manifest),
            "--json-out",
            str(tmp_path / "result.json"),
            "--rows-out",
            str(tmp_path / "rows.json"),
            "--test-id",
            "CT-UNIT-DUP",
        ]
    )

    assert exit_code == 2


def test_run_ct_scenarios_reports_malformed_assertion_shape(tmp_path: Path, monkeypatch) -> None:
    repo_root = tmp_path / "repo"
    monkeypatch.setattr(ct_runner, "ROOT", repo_root)
    helper = repo_root / "scripts" / "write_report.py"
    _write_helper(helper, {"ok": True})
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "ct_scenarios_manifest_v1",
                "suite_id": "unit-suite",
                "target": {"kind": "unit", "id": "unit"},
                "scenarios": [
                    {
                        "test_id": "CT-UNIT-BAD-ASSERTIONS",
                        "description": "bad assertions",
                        "command": [
                            "python",
                            "scripts/write_report.py",
                            "--json-out",
                            "artifacts/conformance/node_gate/ct_unit_bad_assertions.json",
                        ],
                        "timeout_seconds": 30,
                        "assertions": {"json_files": {"path": "artifacts/conformance/node_gate/ct_unit_bad_assertions.json"}},
                        "gate_level": "P0-blocking",
                    }
                ],
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = tmp_path / "result.json"
    rows = tmp_path / "rows.json"

    exit_code = ct_runner.main(
        ["--manifest", str(manifest), "--json-out", str(result), "--rows-out", str(rows)]
    )

    assert exit_code == 1
    report = json.loads(result.read_text(encoding="utf-8"))
    assert report["failing_count"] == 1
    assert report["rows"][0]["failures"] == ["assertions.json_files must be a list"]

def test_test_id_filter_runs_p7_with_authorized_temp_workspace_estate(
    tmp_path: Path, monkeypatch
) -> None:
    import importlib.util

    fixture_spec = importlib.util.spec_from_file_location(
        "_ct_c4_fixture_builder",
        Path(__file__).with_name("test_e4_c4_chain_validation.py"),
    )
    assert fixture_spec is not None and fixture_spec.loader is not None
    fixture_module = importlib.util.module_from_spec(fixture_spec)
    fixture_spec.loader.exec_module(fixture_module)

    production_repo = Path(__file__).resolve().parents[1]
    manifest_payload = json.loads(
        (production_repo / "docs/conformance/ct_scenarios_v1.json").read_text(encoding="utf-8")
    )
    scenario = next(
        scenario
        for scenario in manifest_payload["scenarios"]
        if scenario["test_id"] == "CT-P7-CODEX-GPT55-C4-CHAIN"
    )
    monkeypatch.setenv("BB_WORKSPACE_ROOT", str(tmp_path))
    chain = fixture_module._build_chain(tmp_path)
    temp_repo = Path(chain["repo"])
    monkeypatch.setattr(ct_runner, "ROOT", temp_repo)

    scenario["debug"] = {"allow_absolute_paths": True}
    scenario["command"] = [
        sys.executable,
        str(production_repo / "scripts/validate_e4_c4_chain.py"),
        "--repo-root",
        ".",
        "--config-id",
        str(chain["config_id"]),
        "--support-claim",
        str(Path(chain["support_claim"]).relative_to(temp_repo)),
        "--evidence-manifest",
        str(Path(chain["evidence_manifest"]).relative_to(temp_repo)),
        "--json-out",
        "artifacts/conformance/node_gate/ct_p7_codex_gpt55_c4_chain.json",
    ]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": "ct_scenarios_manifest_v1",
                "suite_id": "authorized-temp-estate",
                "target": {"kind": "fixture", "id": "codex-gpt55-c4"},
                "scenarios": [scenario],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    result = tmp_path / "ct_scenarios_result_v1.json"
    rows = tmp_path / "ct_scenarios_rows_v1.json"

    exit_code = ct_runner.main(
        [
            "--manifest",
            str(manifest),
            "--json-out",
            str(result),
            "--rows-out",
            str(rows),
            "--test-id",
            "CT-P7-CODEX-GPT55-C4-CHAIN",
        ]
    )

    assert exit_code == 0
    report = json.loads(result.read_text(encoding="utf-8"))
    row_payload = json.loads(rows.read_text(encoding="utf-8"))
    c4_report = json.loads(
        (tmp_path / "node_gate/ct_p7_codex_gpt55_c4_chain.json").read_text(encoding="utf-8")
    )
    assert report["ok"] is True
    assert report["status"] == "pass"
    assert report["scenario_count"] == 1
    assert row_payload[0]["test_id"] == "CT-P7-CODEX-GPT55-C4-CHAIN"
    assert row_payload[0]["status"] == "pass"
    assert c4_report["ok"] is True
    assert c4_report["accepted"] is True
    assert c4_report["config_id"] == chain["config_id"]
    assert c4_report["claimed_scope"] == {
        "config_id": "codex_cli_gpt55_e4_capture_probe_v1",
        "lane_id": "codex_cli_e4_capture_probe_v1",
        "provider_model": "gpt-5.5",
        "run_id": "20260630_codex_gpt55_capture_probe",
        "sandbox_mode": "read-only",
        "target_version": "codex-cli 0.139.0",
    }
    assert c4_report["comparator_rerun"] == {
        "assertion_count": 1,
        "comparator_class": "deterministic_replay",
        "comparator_id": "codex_stored_report_replay",
        "missing_assertion_ids": [],
        "ok": True,
        "registry": "conformance/comparators/registry.json",
        "status_mismatch_ids": [],
        "unexpected_assertion_ids": [],
        "value_mismatch_ids": [],
    }
    assert c4_report["errors"] == []


def test_real_manifest_p7_without_workspace_estate_fails_closed(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.delenv("BB_WORKSPACE_ROOT", raising=False)
    result = tmp_path / "ct_scenarios_result_v1.json"
    rows = tmp_path / "ct_scenarios_rows_v1.json"

    exit_code = ct_runner.main(
        [
            "--json-out",
            str(result),
            "--rows-out",
            str(rows),
            "--test-id",
            "CT-P7-CODEX-GPT55-C4-CHAIN",
        ]
    )

    assert exit_code != 0
    report = json.loads(result.read_text(encoding="utf-8"))
    row_payload = json.loads(rows.read_text(encoding="utf-8"))
    assert report["ok"] is False
    assert report["status"] == "fail"
    assert row_payload[0]["test_id"] == "CT-P7-CODEX-GPT55-C4-CHAIN"
    assert row_payload[0]["status"] == "fail"
    assert row_payload[0]["exit_code"] != 0
    assert "BB_WORKSPACE_ROOT is required" in row_payload[0]["stderr_excerpt"]