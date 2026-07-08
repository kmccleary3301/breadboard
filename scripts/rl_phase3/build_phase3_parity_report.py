from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.rl.phase3.evidence import sha256_file
from breadboard.rl.phase3.parity import build_phase3_parity_report, validate_phase3_parity_report


MILESTONE_FILES = {
    "P3-M1": "P3-M1_verl_api_introspection.json",
    "P3-M2": "P3-M2_ppo_weight_update.json",
    "P3-M3": "P3-M3_grpo_weight_update.json",
    "P3-M4": "P3-M4_closed_loop_rollout.json",
}
STAGE_SCRIPTS = {
    "ppo_script": Path("ZYPHRA/RL_PHASE_3/runs/payloads/phase3_container_ppo_8gpu_stage/run.sh"),
    "grpo_script": Path("ZYPHRA/RL_PHASE_3/runs/payloads/phase3_container_grpo_8gpu_stage/run.sh"),
    "closed_loop_script": Path("ZYPHRA/RL_PHASE_3/runs/payloads/closed_loop_verl_train_stage/run.sh"),
}

RUNTIME_INSTALL_REPORTS = (
    Path("ZYPHRA/RL_PHASE_3/runs/phase3_vllm_runtime_parity_probe/phase3_vllm_runtime_parity_probe.json"),
)


def _load(path: Path) -> dict:
    payload = json.loads(path.read_text())
    return payload if isinstance(payload, dict) else {}


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")


def _rel_to_evidence_root(path: Path, evidence_root: Path) -> str:
    return str(path.resolve().relative_to(evidence_root.resolve()))


def _artifact(evidence_root: Path, report: dict, key: str) -> Path:
    raw = report.get("artifact_paths", {}).get(key)
    if not raw:
        raise FileNotFoundError(f"missing artifact path {key}")
    return (evidence_root / raw).resolve()


def _read_script(path: Path) -> str:
    return path.read_text()


def _runtime_evidence(*, evidence_root: Path, p1_report: dict, target_run_id: str) -> dict:
    introspection_artifact = Path(str(p1_report["artifact_paths"]["introspection_report"]))
    introspection_path = (evidence_root / introspection_artifact).resolve()
    scripts = {name: (evidence_root / rel).resolve() for name, rel in STAGE_SCRIPTS.items()}
    texts = {name: _read_script(path) for name, path in scripts.items()}
    images = {match.group(1) for text in texts.values() for match in re.finditer(r'IMAGE="([^"]+)"', text)}
    runtimes = {match.group(1) for text in texts.values() for match in re.finditer(r"source (/shared/[^ ]+)/bin/activate", text)}
    runtime_install_report: Path | None = None
    runtime_install_path: Path | None = None
    runtime_install: dict = {}
    for candidate in RUNTIME_INSTALL_REPORTS:
        candidate_path = (evidence_root / candidate).resolve()
        if not candidate_path.exists():
            continue
        payload = _load(candidate_path)
        if payload.get("target_run_id") != target_run_id:
            continue
        runtime_install_report = candidate
        runtime_install_path = candidate_path
        runtime_install = payload
        break
    runtime_imports = runtime_install.get("imports") if isinstance(runtime_install.get("imports"), dict) else {}
    runtime_install_hash = sha256_file(runtime_install_path) if runtime_install_path and runtime_install_path.exists() else ""
    runtime_install_artifact = str(runtime_install_report) if runtime_install_report and runtime_install_path and runtime_install_path.exists() else ""
    input_hashes = {
        "introspection_report": sha256_file(introspection_path),
        **{name: sha256_file(path) for name, path in scripts.items()},
    }
    if runtime_install_hash:
        input_hashes["runtime_install_report"] = runtime_install_hash
    return {
        "introspection_report_path": str(introspection_path),
        "introspection_report_artifact": str(introspection_artifact),
        "container_image": sorted(images)[0] if len(images) == 1 else "",
        "runtime_path": sorted(runtimes)[0] if len(runtimes) == 1 else "",
        "runtime_install_report_path": str(runtime_install_path) if runtime_install_path and runtime_install_path.exists() else "",
        "runtime_install_report_artifact": runtime_install_artifact,
        "runtime_install_runtime": str(runtime_install.get("runtime") or ""),
        "runtime_install_passed": runtime_install.get("passed") is True,
        "vllm_version": str(runtime_imports.get("vllm") or ""),
        "ppo_script_artifact": str(STAGE_SCRIPTS["ppo_script"]),
        "grpo_script_artifact": str(STAGE_SCRIPTS["grpo_script"]),
        "closed_loop_script_artifact": str(STAGE_SCRIPTS["closed_loop_script"]),
        "input_hashes": input_hashes,
    }


def _attach_parity(report_path: Path, *, parity_path: Path, parity_sha256: str, evidence_root: Path) -> None:
    report = _load(report_path)
    artifact_paths = report.setdefault("artifact_paths", {})
    input_hashes = report.setdefault("input_hashes", {})
    required = report.setdefault("required_artifact_keys", [])
    artifact_paths["parity_report"] = _rel_to_evidence_root(parity_path, evidence_root)
    input_hashes["parity_report"] = parity_sha256
    if "parity_report" not in required:
        required.append("parity_report")
    report["parity_report_id"] = "phase3_parity_report"
    _write(report_path, report)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase-dir", type=Path, required=True)
    parser.add_argument("--target-run-id", default="20260624T040000Z-slurm-243958")
    parser.add_argument("--update-milestones", action="store_true")
    args = parser.parse_args()

    phase_dir = args.phase_dir
    evidence_root = phase_dir.parents[1]
    reports_dir = phase_dir / "runs" / "milestone_reports"
    p1 = _load(reports_dir / MILESTONE_FILES["P3-M1"])
    p2 = _load(reports_dir / MILESTONE_FILES["P3-M2"])
    p3 = _load(reports_dir / MILESTONE_FILES["P3-M3"])
    p4 = _load(reports_dir / MILESTONE_FILES["P3-M4"])
    introspection = _load(_artifact(evidence_root, p1, "introspection_report"))
    runtime_evidence = _runtime_evidence(evidence_root=evidence_root, p1_report=p1, target_run_id=args.target_run_id)
    report = build_phase3_parity_report(
        target_run_id=args.target_run_id,
        ppo_report=p2,
        grpo_report=p3,
        closed_loop_report=p4,
        introspection_report=introspection,
        runtime_evidence=runtime_evidence,
        evidence_root=evidence_root,
    )
    out = phase_dir / "runs" / "parity" / "phase3_parity_report.json"
    _write(out, report)
    errors = validate_phase3_parity_report(report, target_run_id=args.target_run_id, evidence_root=evidence_root)
    if errors:
        print(json.dumps({"report": str(out), "validation_errors": errors, "updated_milestones": False}, sort_keys=True))
        return 1
    if args.update_milestones:
        parity_sha256 = sha256_file(out)
        for filename in (MILESTONE_FILES["P3-M2"], MILESTONE_FILES["P3-M3"], MILESTONE_FILES["P3-M4"]):
            _attach_parity(reports_dir / filename, parity_path=out, parity_sha256=parity_sha256, evidence_root=evidence_root)
    print(json.dumps({"report": str(out), "validation_errors": [], "updated_milestones": bool(args.update_milestones)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
