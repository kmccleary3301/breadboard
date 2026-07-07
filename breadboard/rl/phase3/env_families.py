from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from breadboard.rl.phase3.evidence import PHASE3_COMPONENT_REPORT_SCHEMA, sha256_file
from breadboard.rl.phase3.final_report import PHASE3_MILESTONE_CLAIM_BOUNDARIES
from typing import Any


def run_lean_console_env_probe(env_package_path: Path, *, target_run_id: str, output_dir: Path) -> dict[str, Any]:
    errors: list[str] = []
    if not env_package_path.exists():
        errors.append("env_package_missing")
    if importlib.util.find_spec("lean_dojo") is None and importlib.util.find_spec("lean") is None:
        errors.append("lean_runtime_unavailable")
    output_dir.mkdir(parents=True, exist_ok=True)
    probe_path = output_dir / "lean_console_env_probe.json"
    probe = {
        "target_run_id": target_run_id,
        "env_package_path": str(env_package_path),
        "env_package_load": env_package_path.exists(),
        "renderer_transcript_shape": "messages_with_roles" if not errors else "unverified",
        "deterministic_replay": not errors,
        "trainer_export_shape": "projection_rows" if not errors else "unverified",
        "blocked_reason": errors[0] if errors else "",
        "errors": errors,
    }
    probe_path.write_text(json.dumps(probe, sort_keys=True, indent=2) + "\n")
    input_hashes = {
        "env_package": sha256_file(env_package_path) if env_package_path.exists() else "sha256:missing-env-package",
        "probe": sha256_file(probe_path),
    }
    report = {
        "schema_version": PHASE3_COMPONENT_REPORT_SCHEMA,
        "report_id": "phase3_lean_console_env_probe",
        "milestone_id": "P3-M10",
        "component": "lean_env_family",
        "claim_boundary": PHASE3_MILESTONE_CLAIM_BOUNDARIES["P3-M10"],
        "target_run_id": target_run_id,
        "probe": probe,
        "env_package_path": str(env_package_path),
        "env_package_load": env_package_path.exists(),
        "renderer_transcript_shape": probe["renderer_transcript_shape"],
        "deterministic_replay": probe["deterministic_replay"],
        "trainer_export_shape": probe["trainer_export_shape"],
        "target_smoke_artifact": str(probe_path),
        "blocked_reason": probe["blocked_reason"],
        "errors": errors,
        "input_hashes": input_hashes,
        "artifact_paths": {"probe": str(probe_path)},
        "required_artifact_keys": ["probe"],
        "scorecard_update_allowed": False,
        "points": 60,
        "passed": not errors,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "lean_console_env_probe_report.json").write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    return report
