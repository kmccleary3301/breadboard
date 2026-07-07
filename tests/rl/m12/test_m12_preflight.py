from __future__ import annotations

import json
import hashlib
import subprocess
import sys
from pathlib import Path

from breadboard.rl.m12 import run_m12_preflight, validate_m12_preflight_report, write_m12_preflight_report
from breadboard.rl.m12.preflight import ENV_VALUE_REDACTED_ABSOLUTE_PATH, _safe_environment_snapshot


REPO_ROOT = Path(__file__).resolve().parents[3]


def _fingerprint_sha256(fingerprint: dict) -> str:
    payload = dict(fingerprint)
    payload.pop("sha256", None)
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def test_m12_preflight_is_non_scoring_and_has_completion_gate(tmp_path) -> None:
    report = run_m12_preflight()

    assert report["report_id"] == "bb_zyphra_rl_phase1_m12_preflight_v1"
    assert report["claim_boundary"] == "target_preflight_only_not_m12_validation"
    assert report["status"] in {"blocked", "preflight_passed"}
    assert report["gpu"]["required_accelerator_count"] == 8
    assert report["gpu"]["required_accelerator_family"] == "MI300X"
    assert report["filesystem_cas_smoke"]["status"] == "passed"
    assert report["filesystem_cas_smoke"]["sha256"].startswith("sha256:")
    assert report["ray_cluster"]["module_available"] in {True, False}
    assert report["inference_engine_feasibility"]["decision"] in {"available", "blocked_no_vllm_or_sglang"}
    fingerprint = report["runtime_fingerprint"]
    assert fingerprint["fingerprint_id"] == "bb_zyphra_rl_phase1_m12_runtime_fingerprint_v1"
    assert fingerprint["sha256"].startswith("sha256:")
    assert fingerprint["sha256"] == _fingerprint_sha256(fingerprint)
    assert "/" not in fingerprint["platform"]["python_executable_name"]
    assert (
        fingerprint["sanitized_environment"]["policy"]
        == "allowlist_only_redact_path_values_no_secret_keys_no_absolute_python_paths"
    )
    assert all("KEY" not in key and "TOKEN" not in key and "SECRET" not in key for key in fingerprint["sanitized_environment"]["keys"])
    assert isinstance(fingerprint["sanitized_environment"]["redactions"], dict)
    assert "m12_completion_gate" in report
    assert "final M12 report" in "\n".join(report["m12_completion_gate"])
    assert report["required_next_step"] == "Run on target 8xMI300X node before awarding M12 points."

    written = write_m12_preflight_report(tmp_path)
    persisted = json.loads((tmp_path / "m12_preflight_report.json").read_text(encoding="utf-8"))
    assert persisted["report_id"] == written["report_id"]
    assert persisted["claim_boundary"] == "target_preflight_only_not_m12_validation"
    assert validate_m12_preflight_report(persisted) == []


def test_m12_preflight_env_snapshot_redacts_path_like_values(monkeypatch) -> None:
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "/tmp/private-conda-env")
    monkeypatch.setenv("RAY_ADDRESS", "ray://127.0.0.1:10001")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-appear")

    snapshot = _safe_environment_snapshot()

    assert snapshot["values"]["CONDA_DEFAULT_ENV"] == ENV_VALUE_REDACTED_ABSOLUTE_PATH
    assert snapshot["redactions"]["CONDA_DEFAULT_ENV"] == "absolute_or_home_path"
    assert snapshot["values"]["RAY_ADDRESS"] == "ray://127.0.0.1:10001"
    assert "OPENAI_API_KEY" not in snapshot["values"]
    assert "OPENAI_API_KEY" not in snapshot["redactions"]


def test_m12_preflight_report_validator_rejects_invalid_status_and_blockers() -> None:
    report = run_m12_preflight()
    report["status"] = "preflight_passed"
    report["blockers"] = ["torch_device_count_below_8"]

    assert "preflight_passed requires blockers=[]" in validate_m12_preflight_report(report)

    report["status"] = "blocked"
    report["blockers"] = []
    assert "blocked preflight requires at least one blocker" in validate_m12_preflight_report(report)


def test_m12_preflight_report_validator_rejects_tampered_fingerprint() -> None:
    report = run_m12_preflight()
    report["runtime_fingerprint"]["unexpected_section"] = {"not": "allowed"}

    assert (
        "runtime_fingerprint must be exact-schema, self-hash-verified, path-redacted, and allowlisted"
        in validate_m12_preflight_report(report)
    )


def test_m12_preflight_report_validator_rejects_fingerprint_top_level_drift() -> None:
    report = run_m12_preflight()
    report["container_runtimes"]["docker"] = not report["container_runtimes"]["docker"]

    assert "runtime_fingerprint must match top-level preflight evidence" in validate_m12_preflight_report(report)


def test_m12_preflight_report_validator_rejects_contradictory_passed_report() -> None:
    report = run_m12_preflight()
    report["status"] = "preflight_passed"
    report["blockers"] = []
    report["gpu"]["rocminfo_available"] = False
    report["gpu"]["rocm_smi_available"] = False
    report["gpu"]["mi300x_product_evidence"] = False
    report["gpu"]["torch_probe"]["parse_error"] = True
    report["gpu"]["torch_probe"]["device_count"] = 0
    report["python_modules"]["torch"]["available"] = False
    report["python_modules"]["ray"]["available"] = False
    report["python_modules"]["verl"]["available"] = False
    report["ray_cluster"]["module_available"] = False
    report["inference_engine_feasibility"] = {
        "decision": "blocked_no_vllm_or_sglang",
        "vllm_available": False,
        "sglang_available": False,
    }
    report["container_runtimes"] = {"docker": False, "gvisor_runsc": False, "firecracker": False}
    report["filesystem_cas_smoke"]["status"] = "failed"

    errors = validate_m12_preflight_report(report)

    assert "preflight_passed requires ROCm tooling availability" in errors
    assert "preflight_passed requires torch availability" in errors
    assert "preflight_passed requires parseable torch probe" in errors
    assert "preflight_passed requires torch device_count >= 8" in errors
    assert "preflight_passed requires MI300X product evidence" in errors
    assert "preflight_passed requires Ray availability" in errors
    assert "preflight_passed requires VeRL availability" in errors
    assert "preflight_passed requires vLLM or SGLang availability" in errors
    assert "preflight_passed requires at least one container runtime" in errors
    assert "preflight_passed requires filesystem/CAS smoke passed" in errors


def test_m12_preflight_blocks_when_target_stack_is_absent_here() -> None:
    report = run_m12_preflight()

    if report["status"] == "blocked":
        assert report["blockers"]
    else:
        assert not report["blockers"]
        assert report["gpu"]["torch_probe"]["device_count"] >= 8
        assert report["gpu"]["mi300x_product_evidence"] is True


def test_m12_preflight_require_pass_exits_nonzero_when_blocked(tmp_path) -> None:
    output_dir = tmp_path / "preflight"
    result = subprocess.run(
        [
            sys.executable,
            "scripts/rl_phase1/run_m12_preflight.py",
            "--output-dir",
            str(output_dir),
            "--require-pass",
        ],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    report = json.loads((output_dir / "m12_preflight_report.json").read_text(encoding="utf-8"))

    if report["status"] == "preflight_passed":
        assert result.returncode == 0
    else:
        assert result.returncode == 3
        assert "status=blocked" in result.stdout
