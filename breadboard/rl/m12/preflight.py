from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path, PureWindowsPath
from tempfile import TemporaryDirectory
from typing import Any


SAFE_ENV_KEYS = [
    "CUDA_VISIBLE_DEVICES",
    "HIP_VISIBLE_DEVICES",
    "ROCR_VISIBLE_DEVICES",
    "HSA_VISIBLE_DEVICES",
    "RAY_ADDRESS",
    "CONDA_DEFAULT_ENV",
]
ENV_VALUE_REDACTED_ABSOLUTE_PATH = "<redacted:absolute_or_home_path>"
ENV_REDACTION_REASON_ABSOLUTE_PATH = "absolute_or_home_path"
ENV_SNAPSHOT_POLICY = "allowlist_only_redact_path_values_no_secret_keys_no_absolute_python_paths"
RUNTIME_FINGERPRINT_ID = "bb_zyphra_rl_phase1_m12_runtime_fingerprint_v1"
RUNTIME_FINGERPRINT_KEYS = {
    "fingerprint_id",
    "gpu_summary",
    "platform",
    "python_modules",
    "ray_summary",
    "sanitized_environment",
    "sha256",
    "tool_presence",
}
RUNTIME_FINGERPRINT_PLATFORM_KEYS = {
    "machine",
    "python",
    "python_executable_name",
    "python_implementation",
    "release",
    "system",
}
RUNTIME_FINGERPRINT_TOOL_PRESENCE_KEYS = {
    "docker",
    "firecracker",
    "gvisor_runsc",
    "rocm_smi",
    "rocminfo",
}
RUNTIME_FINGERPRINT_PYTHON_MODULE_KEYS = {"ray", "sglang", "torch", "verl", "vllm"}
RUNTIME_FINGERPRINT_GPU_SUMMARY_KEYS = {"rocm_smi_output", "torch_probe"}
RUNTIME_FINGERPRINT_RAY_SUMMARY_KEYS = {"status_output"}
RUNTIME_FINGERPRINT_SANITIZED_ENVIRONMENT_KEYS = {"keys", "policy", "redactions", "values"}
PYTHON_MODULE_KEYS = {"torch", "ray", "verl", "vllm", "sglang"}
CONTAINER_RUNTIME_KEYS = {"docker", "gvisor_runsc", "firecracker"}
PREFLIGHT_STATUS_VALUES = {"blocked", "preflight_passed"}


def _command_available(command: str) -> bool:
    return shutil.which(command) is not None


def _command_output(command: list[str]) -> str:
    try:
        return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT, timeout=15).strip()
    except Exception as exc:
        return f"unavailable: {type(exc).__name__}: {exc}"


def _env_value_is_path_like(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return False
    if stripped.startswith("~"):
        return True
    return Path(stripped).is_absolute() or PureWindowsPath(stripped).is_absolute()


def _safe_environment_snapshot() -> dict[str, dict[str, str]]:
    values: dict[str, str] = {}
    redactions: dict[str, str] = {}
    for key in SAFE_ENV_KEYS:
        if key not in os.environ:
            continue
        raw_value = str(os.environ[key])
        if _env_value_is_path_like(raw_value):
            values[key] = ENV_VALUE_REDACTED_ABSOLUTE_PATH
            redactions[key] = ENV_REDACTION_REASON_ABSOLUTE_PATH
        else:
            values[key] = raw_value
    return {"values": values, "redactions": redactions}


def _stable_sha256(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def _dict_has_exact_keys(raw: Any, expected_keys: set[str]) -> bool:
    return isinstance(raw, dict) and {str(key) for key in raw.keys()} == expected_keys


def _stable_runtime_fingerprint_sha256(fingerprint: dict[str, Any]) -> str | None:
    payload = dict(fingerprint)
    observed = payload.pop("sha256", None)
    if not isinstance(observed, str) or not observed.startswith("sha256:"):
        return None
    return _stable_sha256(payload)


def _runtime_fingerprint_valid(fingerprint: Any) -> bool:
    if not _dict_has_exact_keys(fingerprint, RUNTIME_FINGERPRINT_KEYS):
        return False
    if fingerprint.get("fingerprint_id") != RUNTIME_FINGERPRINT_ID:
        return False
    if _stable_runtime_fingerprint_sha256(fingerprint) != fingerprint.get("sha256"):
        return False
    if not _dict_has_exact_keys(fingerprint.get("platform"), RUNTIME_FINGERPRINT_PLATFORM_KEYS):
        return False
    if not _dict_has_exact_keys(fingerprint.get("tool_presence"), RUNTIME_FINGERPRINT_TOOL_PRESENCE_KEYS):
        return False
    if not _dict_has_exact_keys(fingerprint.get("python_modules"), RUNTIME_FINGERPRINT_PYTHON_MODULE_KEYS):
        return False
    if not _dict_has_exact_keys(fingerprint.get("gpu_summary"), RUNTIME_FINGERPRINT_GPU_SUMMARY_KEYS):
        return False
    if not _dict_has_exact_keys(fingerprint.get("ray_summary"), RUNTIME_FINGERPRINT_RAY_SUMMARY_KEYS):
        return False
    sanitized = fingerprint.get("sanitized_environment")
    if not _dict_has_exact_keys(sanitized, RUNTIME_FINGERPRINT_SANITIZED_ENVIRONMENT_KEYS):
        return False
    if sanitized.get("policy") != ENV_SNAPSHOT_POLICY:
        return False
    raw_keys = sanitized.get("keys")
    values = sanitized.get("values")
    redactions = sanitized.get("redactions")
    if not isinstance(raw_keys, list) or not isinstance(values, dict) or not isinstance(redactions, dict):
        return False
    keys = {str(key) for key in raw_keys}
    value_keys = {str(key) for key in values}
    redaction_keys = {str(key) for key in redactions}
    if keys != set(SAFE_ENV_KEYS):
        return False
    if not value_keys.issubset(set(SAFE_ENV_KEYS)):
        return False
    if not redaction_keys.issubset(value_keys):
        return False
    forbidden_key_parts = ("KEY", "TOKEN", "SECRET", "PASSWORD", "AUTH")
    if any(part in key.upper() for key in keys | value_keys for part in forbidden_key_parts):
        return False
    for key, raw_value in values.items():
        value = str(raw_value)
        if key in redactions:
            if value != ENV_VALUE_REDACTED_ABSOLUTE_PATH:
                return False
            if redactions.get(key) != ENV_REDACTION_REASON_ABSOLUTE_PATH:
                return False
            continue
        if value == ENV_VALUE_REDACTED_ABSOLUTE_PATH:
            return False
        if _env_value_is_path_like(value):
            return False
    python_executable_name = str((fingerprint.get("platform") or {}).get("python_executable_name") or "")
    if "/" in python_executable_name or "\\" in python_executable_name:
        return False
    return True


def _runtime_fingerprint(
    *,
    torch_info: dict[str, Any],
    ray_info: dict[str, Any],
    verl_info: dict[str, Any],
    vllm_info: dict[str, Any],
    sglang_info: dict[str, Any],
    rocm_smi_available: bool,
    rocminfo_available: bool,
    docker_available: bool,
    runsc_available: bool,
    firecracker_available: bool,
    rocm_smi_output: str,
    torch_probe: dict[str, Any],
    ray_status_output: str,
) -> dict[str, Any]:
    env_snapshot = _safe_environment_snapshot()
    payload: dict[str, Any] = {
        "fingerprint_id": "bb_zyphra_rl_phase1_m12_runtime_fingerprint_v1",
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "python_executable_name": Path(sys.executable).name,
        },
        "tool_presence": {
            "rocm_smi": rocm_smi_available,
            "rocminfo": rocminfo_available,
            "docker": docker_available,
            "gvisor_runsc": runsc_available,
            "firecracker": firecracker_available,
        },
        "python_modules": {
            "torch": torch_info,
            "ray": ray_info,
            "verl": verl_info,
            "vllm": vllm_info,
            "sglang": sglang_info,
        },
        "gpu_summary": {
            "rocm_smi_output": rocm_smi_output,
            "torch_probe": torch_probe,
        },
        "ray_summary": {
            "status_output": ray_status_output,
        },
        "sanitized_environment": {
            "policy": ENV_SNAPSHOT_POLICY,
            "keys": list(SAFE_ENV_KEYS),
            "values": env_snapshot["values"],
            "redactions": env_snapshot["redactions"],
        },
    }
    return {**payload, "sha256": _stable_sha256(payload)}


def _module_info_valid(raw: Any) -> bool:
    return isinstance(raw, dict) and isinstance(raw.get("available"), bool) and "version" in raw


def _module_available(modules: dict[str, Any], key: str) -> bool:
    entry = modules.get(key)
    return isinstance(entry, dict) and entry.get("available") is True


def _int_or_zero(raw: Any) -> int:
    try:
        return int(raw)
    except (TypeError, ValueError):
        return 0


def _runtime_fingerprint_matches_report(report: dict[str, Any]) -> bool:
    fingerprint = report.get("runtime_fingerprint")
    if not isinstance(fingerprint, dict):
        return False
    gpu = report.get("gpu")
    modules = report.get("python_modules")
    ray_cluster = report.get("ray_cluster")
    containers = report.get("container_runtimes")
    if not isinstance(gpu, dict) or not isinstance(modules, dict) or not isinstance(ray_cluster, dict):
        return False
    if not isinstance(containers, dict):
        return False
    expected_tool_presence = {
        "rocm_smi": gpu.get("rocm_smi_available"),
        "rocminfo": gpu.get("rocminfo_available"),
        "docker": containers.get("docker"),
        "gvisor_runsc": containers.get("gvisor_runsc"),
        "firecracker": containers.get("firecracker"),
    }
    expected_gpu_summary = {
        "rocm_smi_output": gpu.get("rocm_smi_output"),
        "torch_probe": gpu.get("torch_probe"),
    }
    expected_ray_summary = {"status_output": ray_cluster.get("status_output")}
    return (
        fingerprint.get("tool_presence") == expected_tool_presence
        and fingerprint.get("python_modules") == modules
        and fingerprint.get("gpu_summary") == expected_gpu_summary
        and fingerprint.get("ray_summary") == expected_ray_summary
    )


def validate_m12_preflight_report(report: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if report.get("report_id") != "bb_zyphra_rl_phase1_m12_preflight_v1":
        errors.append("report_id must be bb_zyphra_rl_phase1_m12_preflight_v1")
    if report.get("claim_boundary") != "target_preflight_only_not_m12_validation":
        errors.append("claim_boundary must remain target_preflight_only_not_m12_validation")
    status = report.get("status")
    blockers = report.get("blockers")
    if status not in PREFLIGHT_STATUS_VALUES:
        errors.append("status must be blocked or preflight_passed")
    if not isinstance(blockers, list) or any(not isinstance(item, str) or not item for item in blockers):
        errors.append("blockers must be a list of non-empty strings")
    elif status == "preflight_passed" and blockers:
        errors.append("preflight_passed requires blockers=[]")
    elif status == "blocked" and not blockers:
        errors.append("blocked preflight requires at least one blocker")

    system = report.get("system")
    if not isinstance(system, dict) or not isinstance(system.get("platform"), str) or not isinstance(system.get("python"), str):
        errors.append("system must include platform and python strings")

    gpu = report.get("gpu")
    if not isinstance(gpu, dict):
        errors.append("gpu must be an object")
    else:
        if gpu.get("required_accelerator_count") != 8:
            errors.append("gpu.required_accelerator_count must be 8")
        if gpu.get("required_accelerator_family") != "MI300X":
            errors.append("gpu.required_accelerator_family must be MI300X")
        for key in ["rocminfo_available", "rocm_smi_available", "mi300x_product_evidence"]:
            if not isinstance(gpu.get(key), bool):
                errors.append(f"gpu.{key} must be boolean")
        if not isinstance(gpu.get("rocm_smi_output"), str):
            errors.append("gpu.rocm_smi_output must be a string")
        torch_probe = gpu.get("torch_probe")
        if not isinstance(torch_probe, dict) or not isinstance(torch_probe.get("parse_error"), bool):
            errors.append("gpu.torch_probe must include parse_error boolean")

    python_modules = report.get("python_modules")
    if not isinstance(python_modules, dict) or {str(key) for key in python_modules.keys()} != PYTHON_MODULE_KEYS:
        errors.append("python_modules must include exactly torch, ray, verl, vllm, and sglang")
    elif any(not _module_info_valid(python_modules.get(key)) for key in PYTHON_MODULE_KEYS):
        errors.append("python_modules entries must include available boolean and version")

    ray_cluster = report.get("ray_cluster")
    if (
        not isinstance(ray_cluster, dict)
        or not isinstance(ray_cluster.get("module_available"), bool)
        or not isinstance(ray_cluster.get("status_output"), str)
    ):
        errors.append("ray_cluster must include module_available boolean and status_output string")

    inference = report.get("inference_engine_feasibility")
    if not isinstance(inference, dict):
        errors.append("inference_engine_feasibility must be an object")
    else:
        for key in ["vllm_available", "sglang_available"]:
            if not isinstance(inference.get(key), bool):
                errors.append(f"inference_engine_feasibility.{key} must be boolean")
        if inference.get("decision") not in {"available", "blocked_no_vllm_or_sglang"}:
            errors.append("inference_engine_feasibility.decision is invalid")

    filesystem = report.get("filesystem_cas_smoke")
    if not isinstance(filesystem, dict):
        errors.append("filesystem_cas_smoke must be an object")
    else:
        if filesystem.get("status") not in {"passed", "failed"}:
            errors.append("filesystem_cas_smoke.status must be passed or failed")
        if not str(filesystem.get("sha256") or "").startswith("sha256:"):
            errors.append("filesystem_cas_smoke.sha256 must be recorded")

    container_runtimes = report.get("container_runtimes")
    if not isinstance(container_runtimes, dict) or {str(key) for key in container_runtimes.keys()} != CONTAINER_RUNTIME_KEYS:
        errors.append("container_runtimes must include exactly docker, gvisor_runsc, and firecracker")
    elif any(not isinstance(container_runtimes.get(key), bool) for key in CONTAINER_RUNTIME_KEYS):
        errors.append("container_runtimes entries must be boolean")

    if not _runtime_fingerprint_valid(report.get("runtime_fingerprint")):
        errors.append("runtime_fingerprint must be exact-schema, self-hash-verified, path-redacted, and allowlisted")
    elif not _runtime_fingerprint_matches_report(report):
        errors.append("runtime_fingerprint must match top-level preflight evidence")
    if status == "preflight_passed":
        gpu = report.get("gpu") if isinstance(report.get("gpu"), dict) else {}
        torch_probe = gpu.get("torch_probe") if isinstance(gpu.get("torch_probe"), dict) else {}
        modules = report.get("python_modules") if isinstance(report.get("python_modules"), dict) else {}
        ray_cluster = report.get("ray_cluster") if isinstance(report.get("ray_cluster"), dict) else {}
        inference = report.get("inference_engine_feasibility") if isinstance(report.get("inference_engine_feasibility"), dict) else {}
        filesystem = report.get("filesystem_cas_smoke") if isinstance(report.get("filesystem_cas_smoke"), dict) else {}
        containers = report.get("container_runtimes") if isinstance(report.get("container_runtimes"), dict) else {}
        if not (gpu.get("rocminfo_available") is True or gpu.get("rocm_smi_available") is True):
            errors.append("preflight_passed requires ROCm tooling availability")
        if not _module_available(modules, "torch"):
            errors.append("preflight_passed requires torch availability")
        if torch_probe.get("parse_error") is True:
            errors.append("preflight_passed requires parseable torch probe")
        if _int_or_zero(torch_probe.get("device_count")) < 8:
            errors.append("preflight_passed requires torch device_count >= 8")
        if gpu.get("mi300x_product_evidence") is not True:
            errors.append("preflight_passed requires MI300X product evidence")
        if not _module_available(modules, "ray") or ray_cluster.get("module_available") is not True:
            errors.append("preflight_passed requires Ray availability")
        if not _module_available(modules, "verl"):
            errors.append("preflight_passed requires VeRL availability")
        if not (
            inference.get("decision") == "available"
            and (inference.get("vllm_available") is True or inference.get("sglang_available") is True)
        ):
            errors.append("preflight_passed requires vLLM or SGLang availability")
        if not any(value is True for value in containers.values()):
            errors.append("preflight_passed requires at least one container runtime")
        if filesystem.get("status") != "passed":
            errors.append("preflight_passed requires filesystem/CAS smoke passed")
    if not isinstance(report.get("m12_completion_gate"), list) or not report["m12_completion_gate"]:
        errors.append("m12_completion_gate must be a non-empty list")
    if report.get("required_next_step") != "Run on target 8xMI300X node before awarding M12 points.":
        errors.append("required_next_step must preserve the M12 target-node boundary")
    return errors


def _module_version(module_name: str) -> dict[str, Any]:
    spec = importlib.util.find_spec(module_name)
    if spec is None:
        return {"available": False, "version": None}
    try:
        module = __import__(module_name)
        return {"available": True, "version": str(getattr(module, "__version__", "unknown"))}
    except Exception as exc:
        return {"available": False, "version": f"import_error:{type(exc).__name__}:{exc}"}


def _torch_probe() -> dict[str, Any]:
    output = _command_output(
        [
            sys.executable,
            "-c",
            (
                "import torch, json; "
                "print(json.dumps({"
                "'cuda_available': torch.cuda.is_available(), "
                "'device_count': torch.cuda.device_count(), "
                "'device_names': [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]"
                "}))"
            ),
        ]
    )
    try:
        parsed = json.loads(output)
    except json.JSONDecodeError:
        return {"raw": output, "parse_error": True}
    return {"raw": output, "parse_error": False, **parsed}


def _rocm_names_indicate_mi300x(rocm_smi_output: str, torch_probe: dict[str, Any]) -> bool:
    haystack = rocm_smi_output + "\n" + "\n".join(str(name) for name in torch_probe.get("device_names", []))
    normalized = haystack.lower()
    return "mi300x" in normalized or "mi300" in normalized


def _filesystem_cas_smoke() -> dict[str, Any]:
    payload = b"breadboard-m12-cas-smoke\n" * 4096
    try:
        with TemporaryDirectory(prefix="bb_m12_cas_smoke_") as tmp:
            path = Path(tmp) / "blob.bin"
            start_write = time.perf_counter()
            path.write_bytes(payload)
            write_ms = (time.perf_counter() - start_write) * 1000

            start_read = time.perf_counter()
            observed = path.read_bytes()
            read_ms = (time.perf_counter() - start_read) * 1000

            start_hash = time.perf_counter()
            digest = "sha256:" + hashlib.sha256(observed).hexdigest()
            hash_ms = (time.perf_counter() - start_hash) * 1000
        return {
            "status": "passed" if observed == payload else "failed",
            "bytes": len(payload),
            "sha256": digest,
            "write_ms": round(write_ms, 3),
            "read_ms": round(read_ms, 3),
            "hash_ms": round(hash_ms, 3),
            "failure_mode": None if observed == payload else "read_bytes_did_not_match_written_payload",
        }
    except Exception as exc:
        return {
            "status": "failed",
            "bytes": len(payload),
            "sha256": None,
            "write_ms": None,
            "read_ms": None,
            "hash_ms": None,
            "failure_mode": f"{type(exc).__name__}: {exc}",
        }


def run_m12_preflight() -> dict[str, Any]:
    torch_info = _module_version("torch")
    ray_info = _module_version("ray")
    verl_info = _module_version("verl")
    vllm_info = _module_version("vllm")
    sglang_info = _module_version("sglang")
    rocm_smi_available = _command_available("rocm-smi")
    rocminfo_available = _command_available("rocminfo")
    docker_available = _command_available("docker")
    runsc_available = _command_available("runsc")
    firecracker_available = _command_available("firecracker")
    filesystem_cas_smoke = _filesystem_cas_smoke()

    torch_probe = _torch_probe() if torch_info["available"] else {"raw": "torch unavailable", "parse_error": False}
    rocm_smi_output = _command_output(["rocm-smi", "--showproductname"]) if rocm_smi_available else "unavailable"
    ray_status_output = _command_output(["ray", "status"]) if _command_available("ray") else "ray CLI unavailable"
    gpu_report = {
        "required_accelerator_count": 8,
        "required_accelerator_family": "MI300X",
        "rocminfo_available": rocminfo_available,
        "rocm_smi_available": rocm_smi_available,
        "rocm_smi_output": rocm_smi_output,
        "torch_probe": torch_probe,
        "mi300x_product_evidence": _rocm_names_indicate_mi300x(rocm_smi_output, torch_probe),
    }

    blockers: list[str] = []
    if not (rocminfo_available or rocm_smi_available):
        blockers.append("rocm_tools_unavailable")
    if not torch_info["available"]:
        blockers.append("torch_unavailable")
    elif torch_probe.get("parse_error"):
        blockers.append("torch_probe_unparseable")
    elif int(torch_probe.get("device_count", 0)) < 8:
        blockers.append("torch_device_count_below_8")
    if rocm_smi_available and not gpu_report["mi300x_product_evidence"]:
        blockers.append("mi300x_product_not_detected")
    if not ray_info["available"]:
        blockers.append("ray_unavailable")
    if not verl_info["available"]:
        blockers.append("verl_unavailable")
    if not (vllm_info["available"] or sglang_info["available"]):
        blockers.append("no_vllm_or_sglang")
    if not (docker_available or runsc_available or firecracker_available):
        blockers.append("no_container_runtime_detected")
    if filesystem_cas_smoke["status"] != "passed":
        blockers.append("filesystem_cas_smoke_failed")

    return {
        "report_id": "bb_zyphra_rl_phase1_m12_preflight_v1",
        "claim_boundary": "target_preflight_only_not_m12_validation",
        "target_run_id": os.environ.get("M12_TARGET_RUN_ID"),
        "status": "blocked" if blockers else "preflight_passed",
        "blockers": blockers,
        "system": {
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "gpu": gpu_report,
        "python_modules": {
            "torch": torch_info,
            "ray": ray_info,
            "verl": verl_info,
            "vllm": vllm_info,
            "sglang": sglang_info,
        },
        "ray_cluster": {
            "module_available": ray_info["available"],
            "status_output": ray_status_output,
        },
        "inference_engine_feasibility": {
            "vllm_available": vllm_info["available"],
            "sglang_available": sglang_info["available"],
            "decision": "available" if (vllm_info["available"] or sglang_info["available"]) else "blocked_no_vllm_or_sglang",
        },
        "filesystem_cas_smoke": filesystem_cas_smoke,
        "container_runtimes": {
            "docker": docker_available,
            "gvisor_runsc": runsc_available,
            "firecracker": firecracker_available,
        },
        "runtime_fingerprint": _runtime_fingerprint(
            torch_info=torch_info,
            ray_info=ray_info,
            verl_info=verl_info,
            vllm_info=vllm_info,
            sglang_info=sglang_info,
            rocm_smi_available=rocm_smi_available,
            rocminfo_available=rocminfo_available,
            docker_available=docker_available,
            runsc_available=runsc_available,
            firecracker_available=firecracker_available,
            rocm_smi_output=rocm_smi_output,
            torch_probe=torch_probe,
            ray_status_output=ray_status_output,
        ),
        "m12_completion_gate": [
            "preflight_passed on target 8xMI300X node",
            "full local test suite passes on target node",
            "controlled SWE probe rerun succeeds on target node",
            "VeRL-shaped export smoke consumer succeeds on target node",
            "Ray warm-pool probe succeeds on target node",
            "final M12 report records hardware/software versions, commands, outputs, and caveats",
        ],
        "required_next_step": "Run on target 8xMI300X node before awarding M12 points.",
    }


def write_m12_preflight_report(output_dir: Path) -> dict[str, Any]:
    report = run_m12_preflight()
    errors = validate_m12_preflight_report(report)
    if errors:
        raise ValueError("invalid M12 preflight report: " + "; ".join(errors))
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "m12_preflight_report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return report
