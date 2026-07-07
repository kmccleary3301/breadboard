from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.rl.phase4.wrapper_identity import collect_wrapper_identity, parse_deps_pins

TARGET_RUN_ID_DEFAULT = "20260624T040000Z-slurm-243958"
RUNNER_CONTRACT_SCHEMA = "bb.rl.phase3.runner_contract.v1"
RUNNER_CONTRACT_ID = "phase3_runner_contract"
RUNNER_CONTRACT_BOUNDARY = "phase3_verl_wrapper_runner_contract_named_target_scope"
RUNNER_CONTRACT_BLOCKED_BOUNDARY = "phase3_verl_wrapper_runner_contract_blocked_scope"


def _sha_bytes(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _sha_file(path: Path) -> str:
    return _sha_bytes(path.read_bytes()) if path.exists() and path.is_file() else ""


def _run_git(args: list[str], cwd: Path) -> str:
    try:
        result = subprocess.run(["git", *args], cwd=cwd, check=False, text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return result.stdout.strip() if result.returncode == 0 else ""


def _directory_digest(root: Path, paths: Iterable[Path]) -> str:
    h = hashlib.sha256()
    any_file = False
    for path in sorted(paths, key=lambda item: str(item.relative_to(root))):
        if not path.is_file():
            continue
        any_file = True
        rel = str(path.relative_to(root)).replace("\\", "/").encode()
        h.update(rel + b"\0" + path.read_bytes() + b"\0")
    return "sha256:" + h.hexdigest() if any_file else ""


def _strip_yaml_inline_comment(value: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and in_double:
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == "#" and not in_single and not in_double and (index == 0 or value[index - 1].isspace()):
            return value[:index].rstrip()
    return value.strip()


def _clean_yaml_scalar(value: str) -> str:
    value = _strip_yaml_inline_comment(value).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]
    return value.strip()


def _parse_deps_yaml(path: Path) -> dict[str, str]:
    return parse_deps_pins(path)


def _submodule_status(wrapper_dir: Path) -> list[dict[str, str]]:
    raw = _run_git(["submodule", "status", "--recursive"], wrapper_dir)
    rows: list[dict[str, str]] = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        marker = line[0] if line[0] in {"-", "+", "U"} else ""
        parts = line.lstrip("-+U ").split()
        if len(parts) >= 2:
            rows.append({"path": parts[1], "commit": parts[0], "marker": marker})
    return rows


def build_contract(
    *, wrapper_dir: Path, target_run_id: str, container_image: str, container_digest: str, launch_command: str, git_runner=_run_git
) -> dict:
    wrapper_dir = wrapper_dir.resolve()
    deps_path = wrapper_dir / "deps.yaml"
    gitmodules_path = wrapper_dir / ".gitmodules"
    identity = collect_wrapper_identity(wrapper_dir, git_runner=git_runner)
    deps = identity.pins
    patch_hash = _directory_digest(wrapper_dir, (wrapper_dir / "patches" / "verl").glob("**/*"))
    recipe_files = []
    for rel in ("src/zyphra_verl", "launch", "gates", "deps.yaml", "pyproject.toml"):
        path = wrapper_dir / rel
        if path.is_dir():
            recipe_files.extend(path.glob("**/*"))
        elif path.exists():
            recipe_files.append(path)
    recipe_hash = _directory_digest(wrapper_dir, recipe_files)
    commit = identity.wrapper_commit
    ref = identity.wrapper_ref
    submodules = list(identity.submodules.values())
    required = {
        "wrapper_commit": commit,
        "verl_pin": identity.components["verl"].expected_commit,
        "verl_commit": identity.components["verl"].actual_commit,
        "nemo_gym_pin": identity.components["nemo_gym"].expected_commit,
        "nemo_gym_commit": identity.components["nemo_gym"].actual_commit,
        "patch_queue_sha256": patch_hash,
        "recipe_package_sha256": recipe_hash,
        "container_digest": container_digest,
        "launch_command": launch_command,
    }
    missing = [key for key, value in required.items() if not value]
    identity_blockers = list(identity.blockers)
    passed = not missing and not identity_blockers
    return {
        "schema_version": RUNNER_CONTRACT_SCHEMA,
        "report_id": RUNNER_CONTRACT_ID,
        "claim_boundary": RUNNER_CONTRACT_BOUNDARY if passed else RUNNER_CONTRACT_BLOCKED_BOUNDARY,
        "target_run_id": target_run_id,
        "component": "runner_contract",
        "wrapper": {
            "path": str(wrapper_dir),
            "commit": commit,
            "ref": ref,
            "gitmodules_path": str(gitmodules_path),
            "gitmodules_sha256": _sha_file(gitmodules_path),
            "deps_yaml_path": str(deps_path),
            "deps_yaml_sha256": _sha_file(deps_path),
            "submodules": submodules,
        },
        "pins": deps,
        "required_identities": required,
        "wrapper_identity": identity.to_dict(),
        "wrapper_identity_blockers": identity_blockers,
        "patch_queue_sha256": patch_hash,
        "recipe_package_sha256": recipe_hash,
        "container_image": container_image,
        "container_digest": container_digest,
        "launch_command": launch_command,
        "missing_required_identities": missing,
        "scorecard_update_allowed": False,
        "passed": passed,
        "blocked_reason": "" if passed else "missing_runner_contract_identity",
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase-dir", required=True, type=Path)
    parser.add_argument("--wrapper-dir", required=True, type=Path)
    parser.add_argument("--target-run-id", default=TARGET_RUN_ID_DEFAULT)
    parser.add_argument("--container-image", default="vllm/vllm-openai-rocm:nightly")
    parser.add_argument("--container-digest", default="")
    parser.add_argument("--launch-command", default="launch/train.sh")
    args = parser.parse_args()
    report = build_contract(
        wrapper_dir=args.wrapper_dir,
        target_run_id=args.target_run_id,
        container_image=args.container_image,
        container_digest=args.container_digest,
        launch_command=args.launch_command,
    )
    output = args.phase_dir / "runs" / "runner_contract" / "phase3_runner_contract.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, sort_keys=True, indent=2) + "\n")
    print(json.dumps({"report": str(output), "passed": report["passed"], "missing": report["missing_required_identities"]}, sort_keys=True))
    return 0 if report["passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
