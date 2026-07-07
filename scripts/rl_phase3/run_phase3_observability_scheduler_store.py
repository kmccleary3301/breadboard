from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.rl.phase3.evidence import normalize_phase3_metric_sources, sha256_file, write_phase3_json
from breadboard.rl.phase3.observability_live import LOCAL_OBJECT_STORE_BACKENDS, build_live_observability_report



def _load(path: Path | None) -> dict:
    return json.loads(path.read_text()) if path and path.exists() else {}




def _object_store_blocker_state(path: Path | None) -> dict:
    if not path or not path.exists():
        return {
            "object_store_metrics_present": False,
            "object_store_backend": "",
            "object_store_is_local": None,
            "production_object_store_endpoint_present": False,
        }
    metrics = _load(path)
    backend = str(metrics.get("object_store") or metrics.get("backend") or "")
    is_local = backend in LOCAL_OBJECT_STORE_BACKENDS
    return {
        "object_store_metrics_present": True,
        "object_store_backend": backend,
        "object_store_is_local": is_local,
        "production_object_store_endpoint_present": bool(backend and not is_local),
    }


def _p3m11_blocker_evidence(*, target_run_id: str, missing: list[str], object_store_metrics: Path | None) -> dict:
    return {
        "schema_version": "bb.rl.phase3.p3m11_blocker_evidence.v1",
        "report_id": "p3m11_blocker_evidence",
        "target_run_id": target_run_id,
        "missing_inputs": missing,
        "controller_env_verifier_base_url_present": bool(os.environ.get("BREADBOARD_VERIFIER_BASE_URL")),
        "controller_env_verifier_token_present": bool(os.environ.get("BREADBOARD_VERIFIER_TOKEN")),
        **_object_store_blocker_state(object_store_metrics),
    }




def _component(*, target_run_id: str, output_dir: Path, passed: bool, blocked_reason: str, evidence: dict, artifact_paths: dict[str, Path]) -> dict:
    input_hashes = {key: sha256_file(path) for key, path in artifact_paths.items()}
    return {
        "schema_version": "bb.rl.phase3.component_report.v1",
        "report_id": "p3-m11_observability_scheduler_store",
        "milestone_id": "P3-M11",
        "component": "observability_scheduler_store",
        "claim_boundary": "phase3_live_observability_object_store_scheduler_scope" if passed else "phase3_observability_scheduler_store_blocked_scope",
        "target_run_id": target_run_id,
        "points": 80,
        "passed": passed,
        "blocked_reason": blocked_reason,
        "observability_evidence": evidence,
        "input_hashes": input_hashes,
        "artifact_paths": {key: str(path) for key, path in artifact_paths.items()},
        "required_artifact_keys": list(artifact_paths),
        "scorecard_update_allowed": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase-dir", required=True, type=Path)
    parser.add_argument("--target-run-id", required=True)
    parser.add_argument("--slurm-metrics", type=Path)
    parser.add_argument("--gpu-metrics", type=Path)
    parser.add_argument("--verifier-metrics", type=Path)
    parser.add_argument("--service-metrics", type=Path)
    parser.add_argument("--object-store-metrics", type=Path)
    parser.add_argument("--scheduler-metrics", type=Path)
    parser.add_argument("--budget-caps", type=Path)
    args = parser.parse_args()
    output_dir = args.phase_dir / "runs" / "observability_scheduler_store_runner"
    output_dir.mkdir(parents=True, exist_ok=True)
    required_inputs = {
        "slurm_metrics": args.slurm_metrics,
        "gpu_metrics": args.gpu_metrics,
        "verifier_metrics": args.verifier_metrics,
        "service_metrics": args.service_metrics,
        "object_store_metrics": args.object_store_metrics,
        "scheduler_metrics": args.scheduler_metrics,
        "budget_caps": args.budget_caps,
    }
    missing = [key for key, path in required_inputs.items() if not path or not path.exists()]
    copied: dict[str, Path] = {}
    for key, path in required_inputs.items():
        if path and path.exists():
            dst = output_dir / f"{key}.json"
            dst.write_text(path.read_text())
            copied[key] = dst
    if missing:
        blocker = output_dir / "p3m11_blocker_evidence.json"
        blocker_evidence = _p3m11_blocker_evidence(target_run_id=args.target_run_id, missing=missing, object_store_metrics=args.object_store_metrics)
        write_phase3_json(blocker, blocker_evidence)
        report = _component(
            target_run_id=args.target_run_id,
            output_dir=output_dir,
            passed=False,
            blocked_reason="missing live observability inputs: " + ",".join(missing),
            evidence=blocker_evidence,
            artifact_paths={"blocker_evidence": blocker, **copied},
        )
        write_phase3_json(output_dir / "P3-M11_observability_scheduler_store.json", report)
        print(json.dumps({"passed": False, "blocked_reason": report["blocked_reason"], "report": str(output_dir / "P3-M11_observability_scheduler_store.json")}, sort_keys=True))
        return 2
    metrics = normalize_phase3_metric_sources(
        {
            "slurm": _load(args.slurm_metrics),
            "gpu": _load(args.gpu_metrics),
            "verifier": _load(args.verifier_metrics),
            "service": _load(args.service_metrics),
            "object_store": _load(args.object_store_metrics),
            "scheduler": _load(args.scheduler_metrics),
        }
    )
    slurm = metrics["slurm"]
    gpu = metrics["gpu"]
    verifier = metrics["verifier"]
    service = metrics["service"]
    object_store = metrics["object_store"]
    scheduler = metrics["scheduler"]
    live = build_live_observability_report(
        target_run_id=args.target_run_id,
        slurm_metrics=slurm,
        gpu_metrics=gpu,
        verifier_metrics=verifier,
        service_metrics=service,
        object_store_metrics=object_store,
        budget_caps=_load(args.budget_caps),
        scheduler_metrics=scheduler,
    )
    live_path = output_dir / "live_observability_report.json"
    write_phase3_json(live_path, live)
    semantic_errors = list(live.get("errors", []))
    report = _component(
        target_run_id=args.target_run_id,
        output_dir=output_dir,
        passed=not semantic_errors,
        blocked_reason=";".join(semantic_errors),
        evidence=live,
        artifact_paths={"live_observability_report": live_path, **copied},
    )
    write_phase3_json(output_dir / "P3-M11_observability_scheduler_store.json", report)
    print(json.dumps({"passed": report["passed"], "blocked_reason": report["blocked_reason"], "report": str(output_dir / "P3-M11_observability_scheduler_store.json")}, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
