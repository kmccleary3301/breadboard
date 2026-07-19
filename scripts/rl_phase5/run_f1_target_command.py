from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import hmac
import io
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import uuid
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

TARGET_ALIAS = "ZYPHRA_IBM_AMD_1"
PARTITION = "gpu"
IMAGE = "python@sha256:b81b4bec9aa047850f17862ce34a3ac99463920ef1e9df0434fd7ccdfe2ca691"
RUNNER_PREFIX = b"F1_RUNNER_ARCHIVE="
RESULT_PREFIX = b"F1_RESULT_ARCHIVE="
_ATTEMPT = re.compile(r"^f1-[a-z0-9-]{8,80}$")


def canon(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def derive_secret_material(seed: bytes) -> tuple[bytes, ...]:
    if len(seed) < 32:
        raise ValueError("secret seed must contain at least 32 bytes")
    def derive(label: bytes, prefix: bytes, size: int) -> bytes:
        return prefix + hmac.new(seed, b"bb.rl.f1/" + label, hashlib.sha256).hexdigest()[:size].encode()
    return (
        derive(b"api-auth", b"fixture-api-", 48),
        derive(b"policy-callback", b"fixture-policy-", 48),
        derive(b"receipt-signing", b"fixture-receipt-", 64),
    )


def _archive(entries: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with gzip.GzipFile(filename="", mode="wb", fileobj=output, compresslevel=9, mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            for name, raw in sorted(entries.items()):
                info = tarfile.TarInfo(name); info.size = len(raw); info.mode = 0o600; info.mtime = 0
                info.uid = info.gid = 0; info.uname = info.gname = ""
                archive.addfile(info, io.BytesIO(raw))
    return output.getvalue()


def _envelope(prefix: bytes, raw: bytes) -> bytes:
    return prefix + canon({"encoding": "base64", "size_bytes": len(raw), "sha256": digest(raw), "payload": base64.b64encode(raw).decode()}) + b"\n"


def _decode_envelope(stdout: bytes, prefix: bytes) -> bytes:
    lines = [line[len(prefix):] for line in stdout.splitlines() if line.startswith(prefix)]
    if len(lines) != 1:
        raise ValueError(f"exactly one {prefix.decode()} envelope required")
    envelope = json.loads(lines[0])
    if set(envelope) != {"encoding", "size_bytes", "sha256", "payload"} or envelope["encoding"] != "base64":
        raise ValueError("invalid archive envelope")
    raw = base64.b64decode(envelope["payload"], validate=True)
    if isinstance(envelope["size_bytes"], bool) or len(raw) != envelope["size_bytes"] or digest(raw) != envelope["sha256"]:
        raise ValueError("archive envelope size/hash mismatch")
    return raw


def _safe_extract_bytes(raw: bytes, destination: Path) -> None:
    destination.mkdir(mode=0o700, parents=True, exist_ok=False)
    try:
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
            seen: set[str] = set()
            for member in archive.getmembers():
                path = PurePosixPath(member.name)
                if member.name != str(path) or path.is_absolute() or ".." in path.parts or member.name in seen or not member.isfile():
                    raise ValueError("unsafe result archive")
                seen.add(member.name)
            archive.extractall(destination, filter="data")
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def decode_result_archive(stdout: bytes, attempt: Path) -> None:
    raw = _decode_envelope(stdout, RESULT_PREFIX)
    archive = attempt / "result.tar.gz"
    archive.write_bytes(raw)
    extracted = attempt / ".artifacts-extracted"
    _safe_extract_bytes(raw, extracted)
    artifacts = attempt / "artifacts"
    if artifacts.exists():
        raise FileExistsError(artifacts)
    os.replace(extracted, artifacts)


def decode_runner_archive(stdout: bytes, attempt: Path) -> None:
    raw = _decode_envelope(stdout, RUNNER_PREFIX)
    archive = attempt / "runner-result.tar.gz"
    archive.write_bytes(raw)
    extracted = attempt / ".runner-extracted"
    _safe_extract_bytes(raw, extracted)
    target_stdout = extracted / "target.stdout"
    target_stderr = extracted / "target.stderr"
    if not target_stdout.is_file() or not target_stderr.is_file():
        raise ValueError("runner archive lacks target streams")
    os.replace(target_stdout, attempt / "target.stdout")
    os.replace(target_stderr, attempt / "target.stderr")
    runner = attempt / "runner"; runner.mkdir(mode=0o700)
    for name in ("scheduler.json", "image-inspect.json", "container-inspect.json", "post-cleanup.json"):
        source = extracted / "runner" / name
        if source.exists():
            os.replace(source, runner / name)
    shutil.rmtree(extracted)




def _command(argv: list[str]) -> dict[str, object]:
    completed = subprocess.run(argv, capture_output=True, check=False)
    return {"exit_code": completed.returncode, "stdout": completed.stdout.decode("utf-8", "replace"), "stderr": completed.stderr.decode("utf-8", "replace")}


def _write_observation(entries: dict[str, bytes], name: str, value: object) -> None:
    entries[f"runner/{name}"] = canon(value)


def remote_run(bundle_root: Path, attempt_id: str, seed_file: Path | None = None) -> int:
    if not _ATTEMPT.fullmatch(attempt_id):
        raise ValueError("invalid attempt id")
    if seed_file is None:
        seed = secrets.token_bytes(32)
    else:
        seed = seed_file.read_bytes()
    if len(seed) != 32:
        raise ValueError("exactly 32 private seed bytes required")
    entries: dict[str, bytes] = {"target.stdout": b"", "target.stderr": b""}
    name = "bb-" + attempt_id
    label = "bb.rl.f1.attempt=" + attempt_id
    container_id = ""
    rc = 125
    remove_exit = 0
    try:
        started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        job_id = os.environ.get("SLURM_JOB_ID", "")
        scontrol = _command(["scontrol", "show", "job", "-o", job_id])
        scheduler = {
            "schema_version": "bb.rl.f1.scheduler-observation.v1",
            "target_alias": TARGET_ALIAS,
            "requested": {"partition": PARTITION, "nodes": 1, "tasks": 1, "gpus": 1},
            "observed": {
                "job_id": job_id,
                "partition": os.environ.get("SLURM_JOB_PARTITION", ""),
                "node_list": os.environ.get("SLURM_JOB_NODELIST", os.environ.get("SLURM_NODELIST", "")),
                "node_count": int(os.environ.get("SLURM_JOB_NUM_NODES", "0")),
                "task_count": int(os.environ.get("SLURM_NTASKS", "0")),
                "gpus_on_node": int(re.match(r"\d+", os.environ.get("SLURM_GPUS_ON_NODE", "0")).group() if re.match(r"\d+", os.environ.get("SLURM_GPUS_ON_NODE", "0")) else "0"),
                "hostname": socket.gethostname(),
            },
            "started_utc": started,
            "scontrol": {"argv": ["scontrol", "show", "job", "-o", job_id], **scontrol},
        }
        _write_observation(entries, "scheduler.json", scheduler)

        pull = _command(["docker", "pull", "--platform=linux/amd64", IMAGE])
        inspect_command = subprocess.run(["docker", "image", "inspect", IMAGE], capture_output=True, check=False)
        if pull["exit_code"] != 0 or inspect_command.returncode != 0:
            raise RuntimeError("immutable image pull/inspect failed")
        inspected = json.loads(inspect_command.stdout)[0]
        image = {
            "schema_version": "bb.rl.f1.image-observation.v1", "requested_ref": IMAGE, "transport": "docker_registry", "pull": pull,
            "inspect": {"id": inspected["Id"], "repo_digests": inspected.get("RepoDigests") or [], "os": inspected["Os"], "architecture": inspected["Architecture"]},
        }
        _write_observation(entries, "image-inspect.json", image)

        cidfile = Path(tempfile.mkstemp(prefix="f1-cid-")[1]); cidfile.unlink()
        create_argv = [
            "docker", "create", "--interactive", "--platform=linux/amd64", "--pull=never", "--cidfile", str(cidfile), "--name", name, "--label", label,
            "--network=bridge", "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=512", "--read-only",
            "--env", "HOME=/tmp", "--env", "PIP_CACHE_DIR=/tmp/pip-cache",
            "--tmpfs", "/tmp:rw,exec,nosuid,size=1073741824", "--tmpfs", "/run:rw,noexec,nosuid,size=67108864",
            "--device=/dev/kfd", "--device=/dev/dri", "--mount", f"type=bind,src={bundle_root},dst=/f1,readonly",
            IMAGE, "sh", "-ceu",
            "python -m pip install --disable-pip-version-check --no-cache-dir --target /tmp/f1-site --require-hashes -r /f1/scripts/rl_phase5/f1_requirements.lock >&2; PYTHONPATH=/tmp/f1-site:/f1 exec python /f1/scripts/rl_phase5/f1_container_entry.py --attempt-id " + attempt_id + " --source-root /f1",
        ]
        created = subprocess.run(create_argv, capture_output=True, check=False)
        if created.returncode != 0:
            raise RuntimeError("docker create failed: " + created.stderr.decode("utf-8", "replace"))
        container_id = cidfile.read_text("ascii").strip()
        cidfile.unlink(missing_ok=True)
        container_raw = subprocess.run(["docker", "inspect", container_id], capture_output=True, check=True)
        observed = json.loads(container_raw.stdout)[0]
        if observed["Config"]["Labels"].get("bb.rl.f1.attempt") != attempt_id:
            raise RuntimeError("container attempt label mismatch")
        start = subprocess.run(["docker", "start", "-a", "-i", container_id], input=seed, capture_output=True, check=False)
        entries["target.stdout"] = start.stdout
        entries["target.stderr"] = start.stderr
        rc = start.returncode
        _write_observation(entries, "container-inspect.json", {
            "schema_version": "bb.rl.f1.container-observation.v1", "container_id": container_id,
            "name": str(observed["Name"]).removeprefix("/"), "label": label,
            "image_id": observed["Image"], "create_exit_code": created.returncode, "start_exit_code": start.returncode,
        })
    except Exception as exc:
        entries["target.stderr"] += ("runner error: " + type(exc).__name__ + ": " + str(exc) + "\n").encode()
    finally:
        if container_id:
            removed = subprocess.run(["docker", "rm", "-f", container_id], capture_output=True, check=False)
            remove_exit = removed.returncode
        by_name = subprocess.run(["docker", "ps", "-a", "--filter", "name=^/" + name + "$", "--format", "{{.ID}}"], capture_output=True, check=False)
        by_label = subprocess.run(["docker", "ps", "-a", "--filter", "label=" + label, "--format", "{{.ID}}"], capture_output=True, check=False)
        _write_observation(entries, "post-cleanup.json", {
            "schema_version": "bb.rl.f1.container-cleanup-observation.v1", "remove_exit_code": remove_exit,
            "name_matches": by_name.stdout.decode().split(), "label_matches": by_label.stdout.decode().split(),
        })
        raw = _archive(entries)
        os.write(1, _envelope(RUNNER_PREFIX, raw))
    return rc




def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the F1 payload inside the Phase 3 Slurm allocation"
    )
    subparsers = parser.add_subparsers(dest="mode", required=True)
    remote = subparsers.add_parser("remote")
    remote.add_argument("--bundle-root", type=Path, required=True)
    remote.add_argument("--attempt-id", required=True)
    remote.add_argument("--seed-file", type=Path)
    args = parser.parse_args()
    return remote_run(args.bundle_root, args.attempt_id, args.seed_file)


if __name__ == "__main__":
    raise SystemExit(main())
