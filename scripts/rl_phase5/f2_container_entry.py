from __future__ import annotations

import argparse
import gzip
import hashlib
import io
import ipaddress
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path

if __package__ in (None, ""):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from breadboard.rl.phase5.f2_terminal import MARKER_PREFIX, MARKER_SCHEMA, TARGET_ARTIFACTS, export_f2_artifacts_from_raw

RESULT_PREFIX = b"F2_RESULT_ARCHIVE="
RUNNER_OBSERVATION_PREFIX = b"F2_RUNNER_OBSERVATION="


def canon(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def write(path: Path, raw: bytes) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(descriptor, raw)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def run(argv: list[str], cwd: Path, env: dict[str, str]) -> dict[str, object]:
    completed = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, check=False)
    return {"argv": argv, "exit_code": completed.returncode, "stdout_sha256": sha(completed.stdout), "stderr_sha256": sha(completed.stderr), "stdout": completed.stdout.decode("utf-8", "replace"), "stderr": completed.stderr.decode("utf-8", "replace")}


def archive(root: Path) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, compresslevel=9, mtime=0) as compressed, tarfile.open(fileobj=compressed, mode="w") as target:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            raw = path.read_bytes()
            info = tarfile.TarInfo(path.relative_to(root).as_posix())
            info.size, info.mode, info.mtime = len(raw), 0o600, 0
            info.uid = info.gid = 0
            info.uname = info.gname = ""
            target.addfile(info, io.BytesIO(raw))
    return output.getvalue()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one production F2 terminal episode")
    parser.add_argument("--attempt-id", required=True)
    parser.add_argument("--target-run-id", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--credential-file", type=Path, required=True)
    parser.add_argument("--wrapper-image-ref", required=True)
    for name in ("network-id", "name", "label", "subnet", "gateway", "harness-port", "fixed-policy-port", "callback-port"):
        parser.add_argument("--bridge-" + name, required=True)
    parser.add_argument("--callback-ca-file", type=Path, required=True)
    parser.add_argument("--policy-tls-trust-authority", type=Path, required=True)
    for name in ("f1-prerequisite-ref", "config-ref", "task-ref", "verifier-ref", "policy-ref"):
        parser.add_argument("--" + name, required=True)
    args = parser.parse_args()
    source = args.source_root.resolve(strict=True)
    credential = args.credential_file.resolve(strict=True)
    if credential.stat().st_mode & 0o077:
        raise PermissionError("credentials must not be group/world accessible")
    authorities = {
        "f1_prerequisite_ref": args.f1_prerequisite_ref,
        "config_ref": args.config_ref,
        "task_ref": args.task_ref,
        "verifier_ref": args.verifier_ref,
        "policy_ref": args.policy_ref,
    }
    if any(not __import__("re").fullmatch(r"sha256:[0-9a-f]{64}", value) for value in authorities.values()):
        raise ValueError("invalid immutable F2 authority")
    if not __import__("re").fullmatch(r"[A-Za-z0-9._:-]+", args.target_run_id) or args.target_run_id.endswith("pending"):
        raise ValueError("concrete target run id required")
    network = ipaddress.ip_network(args.bridge_subnet, strict=True)
    gateway = ipaddress.ip_address(args.bridge_gateway)
    if gateway not in network or not gateway.is_private or gateway.is_loopback:
        raise ValueError("internal bridge gateway authority is invalid")
    if not __import__("re").fullmatch(r"[0-9a-f]{64}", args.bridge_network_id) or not __import__("re").fullmatch(r"[a-z0-9][a-z0-9_.-]{0,62}", args.bridge_name):
        raise ValueError("internal bridge identity is invalid")
    if not args.bridge_label.startswith("bb.rl.f2.network=sha256:") or not __import__("re").fullmatch(r"[a-z0-9][a-z0-9./_-]*@sha256:[0-9a-f]{64}", args.wrapper_image_ref):
        raise ValueError("wrapper network/image authority is invalid")
    harness_port, fixed_policy_port, callback_port = (
        int(args.bridge_harness_port),
        int(args.bridge_fixed_policy_port),
        int(args.bridge_callback_port),
    )
    if any(not 1 <= port <= 65535 for port in (harness_port, fixed_policy_port, callback_port)):
        raise ValueError("bridge service port is invalid")
    callback_ca = args.callback_ca_file.resolve(strict=True)
    if not callback_ca.is_file() or callback_ca.is_symlink():
        raise ValueError("callback CA authority is not a regular file")
    private = Path(tempfile.mkdtemp(prefix="f2-private-"))
    artifacts = Path(tempfile.mkdtemp(prefix="f2-artifacts-"))
    rc = 1
    try:
        raw_evidence = private / "raw-evidence"
        env = {"PATH": os.environ.get("PATH", ""), "PYTHONPATH": str(source), "HOME": str(private), "REPO": str(source), "OUT": str(private / "canonical-eval.jsonl"), "CONCURRENCY": "1", "LIMIT": "1", "NEMO_EVAL_N": "1", "F2_CREDENTIAL_FILE": str(credential), "F2_ATTEMPT_ID": args.attempt_id, "F2_TARGET_RUN_ID": args.target_run_id, "F2_ROW_COUNT": "1", "F2_ROLLOUT_COUNT": "1", "F2_EPISODE_COUNT": "1", "F2_SELECTOR": "ibm-terminal", "F2_OVERLAYS_JSON": "[]", "F2_RAW_EVIDENCE_DIR": str(raw_evidence), **{key.upper(): value for key, value in authorities.items()}}
        env.update({
            "IMG": args.wrapper_image_ref,
            "NEMO_F2_NETWORK_NAME": args.bridge_name,
            "NEMO_F2_NETWORK_ID": args.bridge_network_id,
            "NEMO_F2_NETWORK_LABEL": args.bridge_label,
            "NEMO_F2_NETWORK_SUBNET": str(network),
            "NEMO_F2_NETWORK_GATEWAY": str(gateway),
            "BASE_URL": f"https://{gateway}:{callback_port}/v1",
            "SSL_CERT_FILE": str(callback_ca),
            "BREADBOARD_HARNESS_BASE_URL": f"http://{gateway}:{harness_port}",
        })
        wrapper_work = private / "wrapper-work"; wrapper_work.mkdir(mode=0o700)
        wrapper_output = private / "wrapper-output"; wrapper_output.mkdir(mode=0o700)
        wrapper_cidfile = private / "wrapper.cid"
        env.update({
            "NEMO_EVAL_F2_STRICT": "1",
            "NEMO_F2_WRAPPER_CIDFILE": str(wrapper_cidfile),
            "NEMO_F2_WRAPPER_NAME": "bb-" + args.attempt_id + "-wrapper",
            "NEMO_F2_WRAPPER_LABEL": "bb.rl.f2.attempt=" + args.attempt_id,
            "NEMO_F2_WORK_ROOT": str(wrapper_work),
            "NEMO_F2_OUTPUT_DIR": str(wrapper_output),
            "NEMO_EXISTING_SLURM_STEP": "1",
            "CAPTURE_REQUEST": "1",
            "NEMO_EVAL_FAIL_ON_ERROR": "1",
            "AGENT_NAME": "breadboard",
            "AGENT_TYPE": "responses_api_agents",
            "NEMO_GYM_SERVICE_INCLUDE_SERVERS": "policy_model,breadboard",
            "DOCKER_PULL_POLICY": "never",
        })
        generate = run(["bash", "launch/generate_nemo.sh"], source, env)
        if generate["exit_code"] != 0:
            raise RuntimeError("canonical launch/generate_nemo.sh chain failed")
        records_dir, objects_dir = raw_evidence / "records", raw_evidence / "objects"
        expected_records = set(TARGET_ARTIFACTS) - {"artifact_graph"}
        actual_records = {path.stem for path in records_dir.glob("*.json")} if records_dir.is_dir() and not records_dir.is_symlink() else set()
        if actual_records != expected_records or not objects_dir.is_dir() or objects_dir.is_symlink():
            raise RuntimeError("production raw F2 evidence inventory is not exact")
        records = {name: (records_dir / f"{name}.json").read_bytes() for name in expected_records}
        tls_trust_raw = args.policy_tls_trust_authority.resolve(strict=True).read_bytes()
        tls_trust = json.loads(tls_trust_raw)
        if canon(tls_trust) != tls_trust_raw:
            raise RuntimeError("policy TLS trust authority is not canonical")
        terminal_package = json.loads(records["terminal_package"])
        if terminal_package.get("policy_tls_trust_authority") != tls_trust:
            raise RuntimeError("raw terminal package does not project exact policy TLS trust authority")
        lineage_raw = (raw_evidence / "lineage.json").read_bytes()
        lineage = json.loads(lineage_raw)
        if set(lineage) != {"parents", "roots", "producers"}:
            raise RuntimeError("production raw F2 lineage is invalid")
        raw_objects = {}
        for path in objects_dir.iterdir():
            if path.is_symlink() or not path.is_file() or not __import__("re").fullmatch(r"[0-9a-f]{64}", path.name):
                raise RuntimeError("production raw CAS inventory is unsafe")
            raw_objects["sha256:" + path.name] = path.read_bytes()
        exported_files = export_f2_artifacts_from_raw(
            records=records,
            raw_objects=raw_objects,
            parents={ref: tuple(values) for ref, values in lineage["parents"].items()},
            roots=tuple(lineage["roots"]),
            producers=lineage["producers"],
        )
        markers = []
        by_filename = {filename: name for name, filename in TARGET_ARTIFACTS.items()}
        if set(exported_files) != set(TARGET_ARTIFACTS.values()):
            raise RuntimeError("F2 exporter output inventory is not exact")
        for filename, raw in exported_files.items():
            name = by_filename[filename]
            write(artifacts / filename, raw)
            markers.append({"schema_version": MARKER_SCHEMA, "attempt_id": args.attempt_id, "name": name, "path": "artifacts/" + filename, "sha256": sha(raw), "size": len(raw)})
        runner_observation_raw = (raw_evidence / "runner-observation.json").read_bytes()
        runner_observation = json.loads(runner_observation_raw)
        if canon(runner_observation) != runner_observation_raw or set(runner_observation) != {"image_inspect", "container_inspect", "post_cleanup"}:
            raise RuntimeError("production runner observation is not exact")
        result_raw = archive(artifacts)
        envelope = {"encoding": "base64", "size_bytes": len(result_raw), "sha256": sha(result_raw), "payload": __import__("base64").b64encode(result_raw).decode("ascii")}
        os.write(1, RUNNER_OBSERVATION_PREFIX + runner_observation_raw + b"\n")
        for marker in markers:
            os.write(1, MARKER_PREFIX.encode() + canon(marker) + b"\n")
        os.write(1, RESULT_PREFIX + canon(envelope) + b"\n")
        rc = 0
    finally:
        shutil.rmtree(private, ignore_errors=True)
        shutil.rmtree(artifacts, ignore_errors=True)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
