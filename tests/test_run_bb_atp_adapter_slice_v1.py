from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


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
        "run_id": "run-2026-03-08-minif2f-pack-a",
        "created_at_utc": "2026-03-08T21:00:00Z",
        "owner": "atp-team",
        "purpose": "hilbert_comparison",
        "benchmark": {
            "name": "minif2f_v2",
            "version": {
                "benchmark_git_sha": "abc123",
                "dataset_sha256": "a" * 64,
            },
            "slice": {
                "method": "pack_a_seedproof_sanity_minif2f_v1",
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
            {"system_id": "bb_atp", "config_ref": "agent_configs/atp_bb_aristotle_match_codexmini_v1.yaml"},
            {"system_id": "hilbert", "config_ref": "other_harness_refs/ml-hilbert"},
        ],
        "artifacts": {
            "root_dir": "artifacts/benchmarks/hilbert_comparison_packs_v1/pack_a/cross_system",
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


def _tasks_payload() -> dict:
    return {
        "schema": "breadboard.aristotle_task_inputs.v1",
        "tasks": [
            {
                "task_id": "t1",
                "input_mode": "formal_lean",
                "input_hash": "hash-t1",
                "input_text": "import Mathlib\n\ntheorem t1 : True := by\n  sorry\n",
            },
            {
                "task_id": "t2",
                "input_mode": "formal_lean",
                "input_hash": "hash-t2",
                "input_text": "import Mathlib\n\ntheorem t2 : True := by\n  sorry\n",
            },
        ],
    }


def test_prepare_task_workspace_scaffolds_verifier_helper(tmp_path: Path) -> None:
    module = _load_module("run_bb_atp_adapter_slice_v1_scaffold", "scripts/run_bb_atp_adapter_slice_v1.py")
    prepared = module.prepare_task_workspace(
        task=_tasks_payload()["tasks"][0],
        workspace_root=tmp_path,
        verifier_url="http://127.0.0.1:18001/verify",
    )
    assert prepared.workspace_dir.exists()
    assert prepared.target_path.name == "t1.lean"
    assert "mkdir -p target artifacts result && python - <<'PY'" in prepared.prompt
    assert 'Path("target/t1.lean").write_text' in prepared.prompt
    assert 'requests.post("http://127.0.0.1:18001/verify"' in prepared.prompt


def test_interpret_verifier_payload_detects_sorry_warning() -> None:
    module = _load_module("run_bb_atp_adapter_slice_v1_verify", "scripts/run_bb_atp_adapter_slice_v1.py")
    payload = {
        "results": [
            {
                "error": None,
                "response": {
                    "messages": [
                        {
                            "severity": "warning",
                            "data": "declaration uses 'sorry'",
                        }
                    ]
                },
            }
        ]
    }
    parsed = module._interpret_verifier_payload(payload)
    assert parsed["ok"] is False
    assert parsed["is_valid_no_sorry"] is False
    assert parsed["has_sorry_warning"] is True


def test_candidate_proof_presence_rejects_empty_payload() -> None:
    module = _load_module("run_bb_atp_adapter_slice_v1_candidate", "scripts/run_bb_atp_adapter_slice_v1.py")
    assert module._proof_candidate_is_present(task_id="imo_1977_p6", proof_text="") is False
    assert module._proof_candidate_is_present(task_id="imo_1977_p6", proof_text="import Mathlib\n") is False
    assert module._proof_candidate_is_present(
        task_id="imo_1977_p6",
        proof_text="import Mathlib\n\ntheorem imo_1977_p6 : True := by\n  trivial\n",
    ) is True


def test_proof_preserves_statement_rejects_mutated_goal() -> None:
    module = _load_module("run_bb_atp_adapter_slice_v1_statement", "scripts/run_bb_atp_adapter_slice_v1.py")
    original = "import Mathlib\n\ntheorem t1 (n : Nat) : n = n := by\n  sorry\n"
    mutated = "import Mathlib\n\ntheorem t1 (n : Nat) : True := by\n  trivial\n"
    preserved = "import Mathlib\n\ntheorem t1 (n : Nat) : n = n := by\n  rfl\n"
    assert module._proof_preserves_statement(original_text=original, proof_text=mutated) is False
    assert module._proof_preserves_statement(original_text=original, proof_text=preserved) is True


def test_completion_summary_done_treats_loop_exit_as_terminal() -> None:
    module = _load_module("run_bb_atp_adapter_slice_v1_terminal", "scripts/run_bb_atp_adapter_slice_v1.py")
    assert module._completion_summary_done(None) is False
    assert module._completion_summary_done({"completed": True}) is True
    assert module._completion_summary_done({"reason": "max_steps_exhausted"}) is True
    assert module._completion_summary_done({"exit_kind": "loop_exit"}) is True
    assert module._completion_summary_done({"method": "loop_exit"}) is True
    assert module._completion_summary_done({"reason": "still_running"}) is False


def test_task_specific_guidance_contains_pack_a_hints() -> None:
    module = _load_module("run_bb_atp_adapter_slice_v1_guidance", "scripts/run_bb_atp_adapter_slice_v1.py")
    imo_guidance = module._task_specific_guidance("imo_1977_p6")
    algebra_guidance = module._task_specific_guidance("mathd_algebra_282")
    math_guidance = module._task_specific_guidance("mathd_numbertheory_780")
    assert "StrictMono f" in imo_guidance
    assert "least positive `m`" in imo_guidance
    assert "irrational_pi" in algebra_guidance
    assert "irrational_sqrt_natCast_iff" in algebra_guidance
    assert "`interval_cases m <;> norm_num at h₂ h₃ ⊢ <;> omega`" in math_guidance
    assert "m = 30" in math_guidance
    assert "intro n" in imo_guidance
    assert "bounded-range route" in math_guidance


def test_run_bb_slice_uses_real_runner_contract_with_injected_fakes(tmp_path: Path) -> None:
    module = _load_module("run_bb_atp_adapter_slice_v1_fake", "scripts/run_bb_atp_adapter_slice_v1.py")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest_payload(), indent=2), encoding="utf-8")
    task_inputs_path = tmp_path / "tasks.json"
    task_inputs_path.write_text(json.dumps(_tasks_payload(), indent=2), encoding="utf-8")
    config_path = tmp_path / "fake_config.yaml"
    config_path.write_text("version: 2\n", encoding="utf-8")

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def health(self):
            return {"status": "ok"}

    def fake_runner(*, client, config_path, model, prepared, permission_mode, timeout_s, poll_interval_s):
        if prepared.task_id == "t1":
            prepared.target_path.parent.mkdir(parents=True, exist_ok=True)
            prepared.target_path.write_text("import Mathlib\n\ntheorem t1 : False := by\n  sorry\n", encoding="utf-8")
            return module.TaskExecutionResult(
                session_id="session-t1",
                session_status="completed",
                wall_clock_ms=1234,
                logging_dir=str(prepared.workspace_dir / "logs"),
                timed_out=False,
                completion_summary={"completed": True},
                reward_summary=None,
                metadata={"model": model},
            )
        prepared.target_path.parent.mkdir(parents=True, exist_ok=True)
        prepared.target_path.write_text("import Mathlib\n\ntheorem t2 : True := by\n  sorry\n", encoding="utf-8")
        return module.TaskExecutionResult(
            session_id="session-t2",
            session_status="completed",
            wall_clock_ms=2345,
            logging_dir=str(prepared.workspace_dir / "logs"),
            timed_out=False,
            completion_summary={"completed": True},
            reward_summary=None,
            metadata={"model": model},
        )

    def fake_verifier(*, proof_text, task_id, verifier_url, timeout_s):
        if task_id == "t1":
            return {"results": [{"error": None, "response": {"messages": [], "time": 0.5}}]}
        return {
            "results": [
                {
                    "error": None,
                    "response": {
                        "messages": [{"severity": "warning", "data": "declaration uses 'sorry'"}],
                        "time": 0.7,
                    },
                }
            ]
        }

    original_client = module.BreadboardClient
    original_ensure_engine = module._ensure_engine
    try:
        module.BreadboardClient = FakeClient
        module._ensure_engine = lambda **kwargs: None
        out_path = tmp_path / "rows.jsonl"
        summary_path = tmp_path / "summary.json"
        summary = module.run_bb_slice(
            manifest_path=manifest_path,
            task_inputs_path=task_inputs_path,
            out_path=out_path,
            summary_path=summary_path,
            system_id="bb_atp",
            config_path=str(config_path),
            model="openrouter/openai/gpt-5.4",
            proof_output_dir=str(tmp_path / "proofs"),
            raw_output_dir=str(tmp_path / "raw"),
            workspace_root=str(tmp_path / "workspaces"),
            base_url="http://127.0.0.1:9099",
            start_engine=False,
            engine_host="127.0.0.1",
            engine_port=9099,
            engine_log_level="warning",
            engine_wait_timeout_s=5.0,
            verifier_url="http://127.0.0.1:18001/verify",
            verifier_timeout_s=30,
            permission_mode="bypass",
            task_timeout_s=60,
            poll_interval_s=0.1,
            limit=None,
            task_runner=fake_runner,
            verifier=fake_verifier,
        )
    finally:
        module.BreadboardClient = original_client
        module._ensure_engine = original_ensure_engine

    assert summary["ok"] is True
    assert summary["status_counts"] == {"UNSOLVED": 2}
    rows = [json.loads(line) for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert [row["task_id"] for row in rows] == ["t1", "t2"]
    assert rows[0]["status"] == "UNSOLVED"
    assert rows[0]["error"] == "theorem_statement_mismatch"
    assert rows[1]["status"] == "UNSOLVED"
    assert rows[0]["prover_system"] == "bb_atp"
    assert rows[0]["budget_class"] == "B"
    assert rows[0]["toolchain_id"] == "lean4.12.0_mathlib.deadbeef"
    assert rows[0]["verification_log_digest"]
    assert (tmp_path / "raw" / "t1.json").exists()
