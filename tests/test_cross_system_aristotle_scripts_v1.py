from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _load_module(module_name: str, rel_path: str):
    module_path = _repo_root() / rel_path
    scripts_dir = str((_repo_root() / "scripts").resolve())
    if scripts_dir not in sys.path:
        sys.path.insert(0, scripts_dir)
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _manifest_payload() -> dict:
    return {
        "run_id": "run-2026-02-25-putnam-b1",
        "created_at_utc": "2026-02-25T21:00:00Z",
        "owner": "atp-team",
        "purpose": "pilot_comparison",
        "benchmark": {
            "name": "putnambench_lean",
            "version": {
                "benchmark_git_sha": "abc123",
                "dataset_sha256": "a" * 64,
            },
            "slice": {
                "method": "stratified_random",
                "seed": 1337,
                "n_tasks": 2,
                "task_ids": ["t1", "t2"],
            },
        },
        "toolchain": {
            "lean_version": "4.12.0",
            "mathlib_commit": "deadbeef",
            "docker_image_digest": "sha256:demo",
        },
        "budget": {
            "class": "B",
            "max_candidates": 4,
            "max_repair_rounds": 2,
            "wall_clock_cap_s": 300,
            "cost_cap_usd": 25.0,
        },
        "systems": [
            {"system_id": "bb_atp", "config_ref": "configs/atp/budgetB.yaml"},
            {"system_id": "aristotle_api", "config_ref": "configs/aristotle/default.yaml"},
        ],
        "artifacts": {
            "root_dir": "artifacts/experiments/demo",
            "store_proofs": True,
            "store_logs": True,
            "redact_secrets": True,
        },
        "acceptance": {
            "determinism_reruns": 2,
            "required_fields": [
                "verification_log_digest",
                "toolchain_id",
                "input_hash",
            ],
        },
    }


def test_run_aristotle_adapter_slice_writes_jsonl(tmp_path: Path, monkeypatch) -> None:
    module = _load_module("run_aristotle_adapter_slice_v1_test", "scripts/run_aristotle_adapter_slice_v1.py")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest_payload(), indent=2), encoding="utf-8")

    task_inputs = {
        "schema": "breadboard.aristotle_task_inputs.v1",
        "tasks": [
            {"task_id": "t1", "input_text": "prove t1", "input_mode": "informal"},
            {"task_id": "t2", "input_text": "prove t2", "input_mode": "informal"},
        ],
    }
    tasks_path = tmp_path / "tasks.json"
    tasks_path.write_text(json.dumps(task_inputs, indent=2), encoding="utf-8")

    class _FakeAdapter:
        def __init__(self) -> None:
            pass

        async def run_tasks(self, *, tasks, run, max_concurrency, proof_output_dir, fail_fast):
            return [
                {
                    "task_id": task.task_id,
                    "toolchain_id": run.toolchain_id,
                    "input_hash": "b" * 64,
                    "prover_system": run.prover_system,
                    "budget_class": run.budget_class,
                    "status": "SOLVED",
                    "verification_log_digest": f"digest-{task.task_id}",
                    "run_id": run.run_id,
                }
                for task in tasks
            ]

    monkeypatch.setattr(module, "AristotleProjectAdapter", _FakeAdapter)

    out_path = tmp_path / "results.jsonl"
    summary_path = tmp_path / "summary.json"
    payload = asyncio.run(
        module.run_adapter_slice(
            manifest_path=manifest_path,
            task_inputs_path=tasks_path,
            out_path=out_path,
            summary_path=summary_path,
            system_id="aristotle_api",
            max_concurrency=1,
            poll_interval_seconds=0.1,
            wall_clock_cap_seconds=100,
            proof_output_dir=None,
            raw_output_dir=None,
            fail_fast=False,
            limit=None,
            enforce_compliance=False,
        )
    )

    assert payload["ok"] is True
    assert payload["task_count"] == 2
    assert out_path.exists()
    lines = [line for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["status"] == "SOLVED"


def test_run_cross_system_pilot_bundle_with_generated_candidate(tmp_path: Path, monkeypatch) -> None:
    module = _load_module("run_cross_system_pilot_bundle_v1_test", "scripts/run_cross_system_pilot_bundle_v1.py")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest_payload(), indent=2), encoding="utf-8")

    baseline_rows = [
        {
            "task_id": "t1",
            "toolchain_id": "lean4.12.0_mathlib.deadbeef",
            "input_hash": "1" * 64,
            "prover_system": "bb_atp",
            "budget_class": "B",
            "status": "SOLVED",
            "verification_log_digest": "bb-v1",
        },
        {
            "task_id": "t2",
            "toolchain_id": "lean4.12.0_mathlib.deadbeef",
            "input_hash": "2" * 64,
            "prover_system": "bb_atp",
            "budget_class": "B",
            "status": "UNSOLVED",
            "verification_log_digest": "bb-v2",
        },
    ]
    baseline_path = tmp_path / "baseline.jsonl"
    baseline_path.write_text(
        "\n".join(json.dumps(row, sort_keys=True) for row in baseline_rows) + "\n",
        encoding="utf-8",
    )

    async def _fake_run_adapter_slice(**kwargs):
        result_path = kwargs["out_path"]
        result_path.parent.mkdir(parents=True, exist_ok=True)
        rows = [
            {
                "task_id": "t1",
                "toolchain_id": "lean4.12.0_mathlib.deadbeef",
                "input_hash": "3" * 64,
                "prover_system": "aristotle_api",
                "budget_class": "B",
                "status": "SOLVED",
                "verification_log_digest": "ar-v1",
            },
            {
                "task_id": "t2",
                "toolchain_id": "lean4.12.0_mathlib.deadbeef",
                "input_hash": "4" * 64,
                "prover_system": "aristotle_api",
                "budget_class": "B",
                "status": "SOLVED",
                "verification_log_digest": "ar-v2",
            },
        ]
        result_path.write_text(
            "\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n",
            encoding="utf-8",
        )
        kwargs["summary_path"].write_text(json.dumps({"ok": True}), encoding="utf-8")
        return {"ok": True}

    monkeypatch.setattr(module, "run_adapter_slice", _fake_run_adapter_slice)

    task_inputs_path = tmp_path / "tasks.json"
    task_inputs_path.write_text(json.dumps({"tasks": [{"task_id": "t1", "input_text": "x"}]}), encoding="utf-8")
    validation_out = tmp_path / "validation.json"
    report_json_out = tmp_path / "report.json"
    report_md_out = tmp_path / "report.md"
    aristotle_out = tmp_path / "aristotle.jsonl"
    aristotle_summary = tmp_path / "aristotle_summary.json"

    payload = asyncio.run(
        module._run(
            manifest_path=manifest_path,
            baseline_results=[baseline_path],
            candidate_results=[],
            aristotle_task_inputs=task_inputs_path,
            aristotle_result_out=aristotle_out,
            aristotle_summary_out=aristotle_summary,
            baseline_system="bb_atp",
            candidate_system="aristotle_api",
            allow_incomplete_matrix=False,
            validation_out=validation_out,
            report_json_out=report_json_out,
            report_md_out=report_md_out,
            max_concurrency=1,
            poll_interval_seconds=0.1,
            wall_clock_cap_seconds=60,
            fail_fast=False,
            compliance_scope_path=None,
            enforce_compliance=False,
        )
    )

    assert payload["ok"] is True
    assert validation_out.exists()
    assert report_json_out.exists()
    assert report_md_out.exists()


def test_run_aristotle_adapter_slice_respects_pending_compliance_gate(tmp_path: Path, monkeypatch) -> None:
    module = _load_module("run_aristotle_adapter_slice_v1_compliance_test", "scripts/run_aristotle_adapter_slice_v1.py")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest_payload(), indent=2), encoding="utf-8")

    task_inputs = {
        "schema": "breadboard.aristotle_task_inputs.v1",
        "tasks": [{"task_id": "t1", "input_text": "prove t1", "input_mode": "informal"}],
    }
    tasks_path = tmp_path / "tasks.json"
    tasks_path.write_text(json.dumps(task_inputs, indent=2), encoding="utf-8")

    pending_scope = {
        "status": "pending",
        "permissions": {
            "benchmark_evaluation_public_datasets": {"approved": False},
            "store_outputs_for_reproducibility": {"approved": False},
            "internal_comparative_reporting": {"approved": False},
        },
        "enforcement": {"fail_closed_until_explicit_approval": True},
    }
    scope_path = tmp_path / "scope.json"
    scope_path.write_text(json.dumps(pending_scope, indent=2), encoding="utf-8")

    class _NeverCalledAdapter:
        def __init__(self) -> None:
            raise AssertionError("adapter should not be initialized when compliance fails")

    monkeypatch.setattr(module, "AristotleProjectAdapter", _NeverCalledAdapter)

    with pytest.raises(module.AristotleComplianceError):
        asyncio.run(
            module.run_adapter_slice(
                manifest_path=manifest_path,
                task_inputs_path=tasks_path,
                out_path=tmp_path / "results.jsonl",
                summary_path=tmp_path / "summary.json",
                system_id="aristotle_api",
                max_concurrency=1,
                poll_interval_seconds=0.1,
                wall_clock_cap_seconds=100,
                proof_output_dir=None,
                raw_output_dir=None,
                fail_fast=False,
                limit=None,
                compliance_scope_path=scope_path,
                enforce_compliance=True,
            )
        )
