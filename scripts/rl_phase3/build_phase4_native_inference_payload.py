from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import stat
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.rl.phase4.wrapper_identity import collect_wrapper_identity

REQUIRED_ZIP_ENTRIES = (
    "run.sh",
    "target_phase4_native_inference_lane.py",
    "repo/breadboard/__init__.py",
    "repo/breadboard/rl/__init__.py",
    "repo/breadboard/rl/phase4/__init__.py",
    "repo/breadboard/rl/phase4/native_inference.py",
    "repo/breadboard/rl/phase4/wrapper_identity.py",
    "verl_wrapper/src/zyphra_verl/nemo_gym_loop.py",
    "verl_wrapper/src/zyphra_verl/configs/agent_loops.yaml",
    "verl_wrapper/wrapper_identity.json",
)

REQUIRED_NATIVE_TERMS = (
    "BREADBOARD_NATIVE_INFERENCE_OWNER",
    "NativeInferenceLane",
    "request_id",
    "response_id",
    "backend_token_texts",
    "posthoc_token_ids",
)

FORBIDDEN_TARGET_TERMS = (
    "fake_manager",
    "probe_manager",
    "Codex reroute",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n")


def copy_file(src: Path, dst: Path, *, executable: bool = False) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if executable:
        mode = dst.stat().st_mode
        dst.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def render_run_sh() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
HIP_DEVICES="${HIP_VISIBLE_DEVICES:-${ROCR_VISIBLE_DEVICES:-0}}"
if [ -z "$HIP_DEVICES" ]; then HIP_DEVICES=0; fi
mkdir -p "$SCRIPT_DIR/.hf_home" "$SCRIPT_DIR/.phase4_native_logs" "$SCRIPT_DIR/.phase4_native_venv_cache"
docker run --rm --ipc=host \
  --cap-add=SYS_PTRACE --security-opt seccomp=unconfined \
  --device=/dev/kfd --device=/dev/dri --group-add video \
  -e HIP_VISIBLE_DEVICES="$HIP_DEVICES" \
  -e ROCR_VISIBLE_DEVICES="${ROCR_VISIBLE_DEVICES:-$HIP_DEVICES}" \
  -e SLURM_JOB_ID="${SLURM_JOB_ID:-}" \
  -e SLURMD_NODENAME="${SLURMD_NODENAME:-$(hostname)}" \
  -e HF_HOME=/workspace/.hf_home \
  -e PHASE3_TARGET_RUN_ID="${PHASE3_TARGET_RUN_ID:-}" \
  -e PHASE3_SLURM_JOB_ID="${SLURM_JOB_ID:-}" \
  -e ZYPHRA_NEMO_GYM_DIR=/workspace/verl_wrapper/third_party/nemo-gym \
  -e PHASE4_NATIVE_INFERENCE_LOG_DIR=/workspace/.phase4_native_logs \
  -e PHASE4_NATIVE_INFERENCE_MODEL="${PHASE4_NATIVE_INFERENCE_MODEL:-Qwen/Qwen2.5-0.5B-Instruct}" \
  -v "$SCRIPT_DIR":/workspace \
  --entrypoint bash \
  vllm/vllm-openai-rocm:nightly \
  -lc 'set -euo pipefail
python -m venv /workspace/.phase4_native_venv
source /workspace/.phase4_native_venv/bin/activate
python -m pip install --upgrade --quiet pip setuptools wheel
python -m pip install --quiet yappi gprof2dot pydot requests omegaconf ray transformers tensordict
if [ ! -e /workspace/verl_wrapper/wrapper_identity.json ]; then
  echo "PHASE4_BLOCKED_REASON=missing_exact_wrapper_dependency:/workspace/verl_wrapper/wrapper_identity.json" >&2
  exit 93
fi
for required_dir in /workspace/verl_wrapper/third_party/verl /workspace/verl_wrapper/third_party/nemo-gym; do
  if [ ! -f "$required_dir/pyproject.toml" ] && [ ! -f "$required_dir/setup.py" ]; then
    echo "PHASE4_BLOCKED_REASON=missing_exact_wrapper_dependency:${required_dir}" >&2
    exit 93
  fi
done
python -m pip install --quiet -e /workspace/verl_wrapper
python -m pip install --quiet -e /workspace/verl_wrapper/third_party/verl
python -m pip install --quiet -e /workspace/verl_wrapper/third_party/nemo-gym
export PYTHONPATH=/workspace/repo:/workspace/verl_wrapper/src:/workspace/verl_wrapper/third_party/verl:/workspace/verl_wrapper/third_party/nemo-gym:${PYTHONPATH:-}
python /workspace/target_phase4_native_inference_lane.py /workspace/verl_wrapper'
"""


def make_zip(stage_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(stage_dir.rglob("*")):
            if path.is_dir() or "__pycache__" in path.parts or path.suffix == ".pyc":
                continue
            archive.write(path, path.relative_to(stage_dir).as_posix())


def validate_payload(stage_dir: Path, zip_path: Path) -> tuple[bool, list[str], dict[str, str]]:
    errors: list[str] = []
    hashes: dict[str, str] = {}
    with zipfile.ZipFile(zip_path) as archive:
        names = set(archive.namelist())
    for entry in REQUIRED_ZIP_ENTRIES:
        if entry not in names:
            errors.append(f"missing zip entry: {entry}")
    run_sh = stage_dir / "run.sh"
    if not run_sh.exists() or not (run_sh.stat().st_mode & stat.S_IXUSR):
        errors.append("run.sh must exist and be executable")
    run_sh_text = run_sh.read_text() if run_sh.exists() else ""
    if "/shared/bb-p3-root" in run_sh_text:
        errors.append("run.sh must not depend on IBM /shared/bb-p3-root")
    for term in (
        "HF_HOME=/workspace/.hf_home",
        "ZYPHRA_NEMO_GYM_DIR=/workspace/verl_wrapper/third_party/nemo-gym",
        "PHASE4_NATIVE_INFERENCE_MODEL=\"${PHASE4_NATIVE_INFERENCE_MODEL:-Qwen/Qwen2.5-0.5B-Instruct}\"",
        "PYTHONPATH=/workspace/repo:/workspace/verl_wrapper/src:/workspace/verl_wrapper/third_party/verl:/workspace/verl_wrapper/third_party/nemo-gym",
    ):
        if term not in run_sh_text:
            errors.append(f"required DO-local run.sh term missing: {term}")
    native_source = (stage_dir / "repo/breadboard/rl/phase4/native_inference.py").read_text()
    target_source = (stage_dir / "target_phase4_native_inference_lane.py").read_text()
    for term in REQUIRED_NATIVE_TERMS:
        if term not in native_source and term not in target_source:
            errors.append(f"required native term missing: {term}")
    for term in FORBIDDEN_TARGET_TERMS:
        if term in native_source or term in target_source:
            errors.append(f"forbidden target term present: {term}")
    wrapper_identity_path = stage_dir / "verl_wrapper/wrapper_identity.json"
    if not wrapper_identity_path.exists():
        errors.append("missing wrapper identity manifest")
    else:
        try:
            wrapper_identity = json.loads(wrapper_identity_path.read_text())
            if wrapper_identity.get("passed") is not True:
                errors.append("wrapper identity manifest must pass")
        except json.JSONDecodeError:
            errors.append("wrapper identity manifest must be valid JSON")
    for entry in REQUIRED_ZIP_ENTRIES:
        file_path = stage_dir / entry
        if file_path.exists() and file_path.is_file():
            hashes[entry] = sha256_file(file_path)
    hashes[zip_path.name] = sha256_file(zip_path)
    return not errors, errors, hashes


def build_payload(*, repo_root: Path, source_payload_dir: Path, output_dir: Path, stamp: str, git_runner=None) -> dict[str, Any]:
    stage_dir = output_dir / f"native_breadboard_inference_lane_{stamp}"
    zip_path = output_dir / f"phase4_native_breadboard_inference_lane_{stamp}.zip"
    if stage_dir.exists():
        shutil.rmtree(stage_dir)
    stage_dir.mkdir(parents=True)
    (stage_dir / "run.sh").write_text(render_run_sh())
    (stage_dir / "run.sh").chmod(0o755)
    copy_file(repo_root / "scripts/rl_phase3/target_phase4_native_inference_lane.py", stage_dir / "target_phase4_native_inference_lane.py")
    for rel in (
        "breadboard/__init__.py",
        "breadboard/rl/__init__.py",
        "breadboard/rl/phase4/__init__.py",
        "breadboard/rl/phase4/native_inference.py",
        "breadboard/rl/phase4/wrapper_identity.py",
    ):
        copy_file(repo_root / rel, stage_dir / "repo" / rel)
    wrapper_src = source_payload_dir / "verl_wrapper"
    wrapper_dst = stage_dir / "verl_wrapper"
    shutil.copytree(wrapper_src, wrapper_dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    wrapper_identity = collect_wrapper_identity(wrapper_src, git_runner=git_runner) if git_runner is not None else collect_wrapper_identity(wrapper_src)
    write_json(wrapper_dst / "wrapper_identity.json", wrapper_identity.to_dict())
    make_zip(stage_dir, zip_path)
    passed, errors, hashes = validate_payload(stage_dir, zip_path)
    report = {
        "schema_version": "bb.rl.phase4.native_inference_payload_build.v1",
        "report_id": f"phase4_native_breadboard_inference_payload_build_{stamp}",
        "built_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "promotional": False,
        "scorecard_update_allowed": False,
        "claim_boundary": "phase4_native_breadboard_sub_inference_lane_payload_build_scope",
        "source_payload_dir": str(source_payload_dir),
        "stage_dir": str(stage_dir),
        "zip_path": str(zip_path),
        "required_entries": list(REQUIRED_ZIP_ENTRIES),
        "hashes": hashes,
        "wrapper_identity": wrapper_identity.to_dict(),
        "errors": errors,
        "passed": passed,
    }
    report_path = output_dir / f"phase4_native_breadboard_inference_payload_build_{stamp}.json"
    write_json(report_path, report)
    report["report_path"] = str(report_path)
    report["report_sha256"] = sha256_file(report_path)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[2])
    parser.add_argument("--source-payload-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--stamp", default=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"))
    args = parser.parse_args(argv)
    report = build_payload(
        repo_root=args.repo_root.resolve(),
        source_payload_dir=args.source_payload_dir.resolve(),
        output_dir=args.output_dir.resolve(),
        stamp=args.stamp,
    )
    print(json.dumps(report, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
