from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
import subprocess
import sys
import threading
import zipfile
from pathlib import Path

import pytest

from breadboard.rl.phase5.transport_smoke_payload import (
    construct_transport_smoke_payload,
)
from scripts.rl_phase3 import run_phase3_target_command_impl as target_command_runner
from scripts.rl_phase5 import build_transport_smoke_payload as payload_builder

_RUNNER_SOURCE_SHA256 = "sha256:" + "1" * 64
_RUNNER_TEST_SHA256 = "sha256:" + "2" * 64
_COMMAND_ID = "transport-smoke-r1"
_REQUESTED_TARGET_RUN_ID = "20260716T120000Z-slurm-pending"
_JOB_ID = "12345"
_FINAL_TARGET_RUN_ID = "20260716T120000Z-slurm-12345"
_NODE_NAME = "node-a01"
_FIXED_NONCE_SHA256 = (
    "sha256:10c8891ef057c347dca254e9325220c26ba81069b6741ec814de098e81f3c873"
)
_NONCLAIMS = [
    "outer transport authority",
    "campaign admission",
    "target execution, scheduling, or placement authority",
    "restart, retry, fetch, or cleanup",
    "F2/F3/F4/F5/F7/F8",
    "GPU or topology beyond the observed one-node/one-task cardinality",
    "model, reward, training, or checkpoint",
    "score, promotion, or external acceptance",
]
_PAYLOAD_NAME = "transport-smoke-payload.zip"
_RECEIPT_NAME = "transport-smoke-payload-build.json"
_MANIFEST_NAME = "payload_manifest.json"
_REPO_ROOT = Path(__file__).resolve().parents[3]
_RUNTIME_ENV_KEYS = (
    "PHASE3_COMMAND_ID",
    "PHASE3_TARGET_RUN_ID",
    "PHASE3_SLURM_JOB_ID",
    "PHASE3_PAYLOAD_ZIP_SHA256",
    "SLURM_JOB_ID",
    "SLURM_NNODES",
    "SLURM_NTASKS",
    "SLURMD_NODENAME",
)

_BUILD_PROCESS = """
from pathlib import Path
import sys
from scripts.rl_phase5.build_transport_smoke_payload import build
build(
    destination=Path(sys.argv[1]),
    command_id=sys.argv[2],
    requested_target_run_id=sys.argv[3],
    runner_source_sha256=sys.argv[4],
    runner_test_sha256=sys.argv[5],
)
"""

_CONCURRENT_BUILD_PROCESS = """
from pathlib import Path
import sys
import time
from scripts.rl_phase5.build_transport_smoke_payload import build
while not Path(sys.argv[2]).exists():
    time.sleep(0.001)
try:
    build(
        destination=Path(sys.argv[1]),
        command_id=sys.argv[3],
        requested_target_run_id=sys.argv[4],
        runner_source_sha256=sys.argv[5],
        runner_test_sha256=sys.argv[6],
    )
except FileExistsError:
    print("lost")
else:
    print("published")
"""


_CRASH_BUILD_PROCESS = """
import os
from pathlib import Path
import sys
from scripts.rl_phase5 import build_transport_smoke_payload as builder

crash_stage = sys.argv[2]
real_fsync = builder._fsync
real_checkpoint = builder._publication_checkpoint

def crashing_fsync(fd, stage):
    real_fsync(fd, stage)
    if stage == crash_stage:
        os._exit(91)

def crashing_checkpoint(stage):
    real_checkpoint(stage)
    if stage == crash_stage:
        os._exit(91)

builder._fsync = crashing_fsync
builder._publication_checkpoint = crashing_checkpoint
builder.build(
    destination=Path(sys.argv[1]),
    command_id=sys.argv[3],
    requested_target_run_id=sys.argv[4],
    runner_source_sha256=sys.argv[5],
    runner_test_sha256=sys.argv[6],
)
"""

_EFFECT_DENIAL_HARNESS = r"""
import builtins
import hashlib
import json
import os
import platform
import re
import socket
import sys

denials = []


def deny(effect):
    denials.append(effect)
    raise PermissionError("embedded effect denied: " + effect)


blocked_import_roots = {
    "asyncio",
    "concurrent",
    "ctypes",
    "ftplib",
    "http",
    "importlib",
    "multiprocessing",
    "pty",
    "smtplib",
    "socket",
    "ssl",
    "subprocess",
    "urllib",
}
real_import = builtins.__import__


def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".", 1)[0]
    if root in blocked_import_roots:
        deny("import:" + name)
    return real_import(name, globals, locals, fromlist, level)


builtins.__import__ = guarded_import
real_open = builtins.open


def guarded_open(file, mode="r", *args, **kwargs):
    if any(flag in mode for flag in "wax+"):
        deny("open-write:" + os.fspath(file))
    return real_open(file, mode, *args, **kwargs)


builtins.open = guarded_open
real_os_open = os.open
write_flags = os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND


def guarded_os_open(path, flags, *args, **kwargs):
    if flags & write_flags:
        deny("os.open-write:" + os.fspath(path))
    return real_os_open(path, flags, *args, **kwargs)


os.open = guarded_os_open


def denied_os_call(name):
    def denied(*args, **kwargs):
        deny("os." + name)
    return denied


for name in (
    "chmod",
    "chown",
    "chflags",
    "creat",
    "execv",
    "execve",
    "execvp",
    "execvpe",
    "fchmod",
    "fchflags",
    "fchown",
    "fork",
    "forkpty",
    "ftruncate",
    "lchown",
    "kill",
    "killpg",
    "link",
    "makedirs",
    "mkdir",
    "mkfifo",
    "mknod",
    "posix_spawn",
    "posix_spawnp",
    "removexattr",
    "remove",
    "removedirs",
    "rename",
    "renames",
    "replace",
    "rmdir",
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
    "symlink",
    "setxattr",
    "system",
    "truncate",
    "unlink",
    "utime",
    "write",
):
    if hasattr(os, name):
        setattr(os, name, denied_os_call(name))


def audit(event, args):
    if event == "open":
        mode = args[1] or ""
        flags = args[2] if len(args) > 2 else 0
        if any(flag in mode for flag in "wax+") or flags & write_flags:
            deny("audit:" + event)
    if (
        event.startswith("socket.")
        or event.startswith("subprocess.")
        or event.startswith("ctypes.")
        or event in {
            "os.chflags",
            "os.chmod",
            "os.chown",
            "os.exec",
            "os.fork",
            "os.forkpty",
            "os.kill",
            "os.killpg",
            "os.posix_spawn",
            "os.removexattr",
            "os.setxattr",
            "os.spawn",
            "os.system",
            "pty.spawn",
        }
    ):
        deny("audit:" + event)


sys.addaudithook(audit)
try:
    program_path = sys.argv[1]
    with real_open(program_path, "rb") as handle:
        program_raw = handle.read()
    sys.argv = [program_path]
    namespace = {"__file__": program_path, "__name__": "__main__"}
    exec(compile(program_raw, program_path, "exec"), namespace, namespace)
except SystemExit as exc:
    if exc.code not in (None, 0):
        raise
finally:
    sys.stderr.write(
        "EFFECT_DENIAL_LOG="
        + json.dumps(denials, separators=(",", ":"))
        + "\n"
    )
"""


class _PrimaryPublicationError(OSError):
    pass



def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode()


def _sha256(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _payload(directory: Path) -> Path:
    return directory / _PAYLOAD_NAME


def _receipt_path(directory: Path) -> Path:
    return directory / _RECEIPT_NAME


def _receipt(directory: Path) -> dict:
    raw = _receipt_path(directory).read_bytes()
    parsed = json.loads(raw)
    assert raw == _canonical(parsed)
    return parsed


def _build(destination: Path, **overrides: str) -> dict:
    arguments = {
        "destination": destination,
        "command_id": _COMMAND_ID,
        "requested_target_run_id": _REQUESTED_TARGET_RUN_ID,
        "runner_source_sha256": _RUNNER_SOURCE_SHA256,
        "runner_test_sha256": _RUNNER_TEST_SHA256,
    }
    arguments.update(overrides)
    return payload_builder.build(**arguments)


def _manifest(directory: Path) -> tuple[dict, bytes]:
    with zipfile.ZipFile(_payload(directory)) as archive:
        raw = archive.read(_MANIFEST_NAME)
    parsed = json.loads(raw)
    assert raw == _canonical(parsed)
    return parsed, raw


def _extract(directory: Path, extracted: Path) -> Path:
    with zipfile.ZipFile(_payload(directory)) as archive:
        archive.extractall(extracted)
    run_sh = extracted / "run.sh"
    run_sh.chmod(0o500)
    return run_sh


def _valid_environment(directory: Path) -> dict[str, str]:
    return {
        "PHASE3_COMMAND_ID": _COMMAND_ID,
        "PHASE3_TARGET_RUN_ID": _FINAL_TARGET_RUN_ID,
        "PHASE3_SLURM_JOB_ID": _JOB_ID,
        "PHASE3_PAYLOAD_ZIP_SHA256": _sha256(_payload(directory).read_bytes()),
        "SLURM_JOB_ID": _JOB_ID,
        "SLURM_NNODES": "1",
        "SLURM_NTASKS": "1",
        "SLURMD_NODENAME": _NODE_NAME,
    }


def _run_smoke(
    run_sh: Path,
    *,
    environment: dict[str, str],
    cwd: Path,
    arguments: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(run_sh), *arguments],
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )


def _run_smoke_with_effect_denials(
    program: Path,
    *,
    environment: dict[str, str],
    cwd: Path,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            _EFFECT_DENIAL_HARNESS,
            str(program),
        ],
        cwd=cwd,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    marker = "EFFECT_DENIAL_LOG="
    stderr_lines = completed.stderr.splitlines()
    marker_lines = [
        (index, line)
        for index, line in enumerate(stderr_lines)
        if line.startswith(marker)
    ]
    assert len(marker_lines) == 1
    marker_index, marker_line = marker_lines[0]
    denial_log = json.loads(marker_line.removeprefix(marker))
    sanitized = subprocess.CompletedProcess(
        args=completed.args,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr="\n".join(
            line for index, line in enumerate(stderr_lines) if index != marker_index
        ),
    )
    return sanitized, denial_log


def _install_publication_pause(
    monkeypatch: pytest.MonkeyPatch,
    *,
    stage: str,
    reached: threading.Event,
    release: threading.Event,
) -> None:
    real_fsync = payload_builder._fsync
    real_checkpoint = payload_builder._publication_checkpoint
    paused = False

    def pause_once() -> None:
        nonlocal paused
        if paused:
            return
        paused = True
        reached.set()
        if not release.wait(timeout=10):
            raise AssertionError("publication pause was not released: " + stage)

    def pausing_fsync(fd: int, observed_stage: str) -> None:
        if stage == observed_stage:
            pause_once()
        real_fsync(fd, observed_stage)

    def pausing_checkpoint(observed_stage: str) -> None:
        real_checkpoint(observed_stage)
        if stage == observed_stage:
            pause_once()

    monkeypatch.setattr(payload_builder, "_fsync", pausing_fsync)
    monkeypatch.setattr(
        payload_builder,
        "_publication_checkpoint",
        pausing_checkpoint,
    )


_ALLOWED_EMBEDDED_IMPORTS = {"hashlib", "json", "os", "platform", "re"}
_QUALIFIED_CALL_ROOTS = _ALLOWED_EMBEDDED_IMPORTS | {"bytes"}
_ALLOWED_EMBEDDED_CALL_FORMS = {
    ".encode",
    ".endswith",
    ".hexdigest",
    "RuntimeError",
    "SystemExit",
    "bytes.fromhex",
    "canonical",
    "hashlib.sha256",
    "json.dumps",
    "len",
    "main",
    "os.environ.get",
    "platform.python_implementation",
    "platform.python_version",
    "print",
    "re.fullmatch",
    "required",
    "sha256",
}


def _embedded_call_form(function: ast.expr) -> str:
    if isinstance(function, ast.Name):
        return function.id
    if not isinstance(function, ast.Attribute):
        return "<dynamic>"
    parts = [function.attr]
    value = function.value
    while isinstance(value, ast.Attribute):
        parts.append(value.attr)
        value = value.value
    if isinstance(value, ast.Name) and value.id in _QUALIFIED_CALL_ROOTS:
        parts.append(value.id)
        return ".".join(reversed(parts))
    return "." + function.attr


def _embedded_effect_policy(
    source: str,
) -> tuple[set[str], set[str], set[str]]:
    tree = ast.parse(source)
    imports: set[str] = set()
    call_forms: set[str] = set()
    violations: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module != "__future__":
            imports.add(node.module or "")
        elif isinstance(node, ast.Call):
            call_form = _embedded_call_form(node.func)
            call_forms.add(call_form)
            if call_form not in _ALLOWED_EMBEDDED_CALL_FORMS:
                violations.add("call-form:" + call_form)
    violations.update(
        "import:" + name
        for name in imports
        if name.split(".", 1)[0] not in _ALLOWED_EMBEDDED_IMPORTS
    )
    return imports, call_forms, violations


def _report_from_success(completed: subprocess.CompletedProcess[str]) -> dict:
    assert completed.returncode == 0
    assert completed.stderr == ""
    lines = completed.stdout.splitlines()
    assert len(lines) == 2
    assert lines[0] == "TRANSPORT_SMOKE_NONCE_SHA256=" + _FIXED_NONCE_SHA256
    prefix = "PHASE3_COMPONENT_REPORT_JSON="
    assert lines[1].startswith(prefix)
    report = json.loads(lines[1].removeprefix(prefix))
    assert completed.stdout == (
        lines[0] + "\n" + prefix + json.dumps(
            report, sort_keys=True, separators=(",", ":")
        ) + "\n"
    )
    return report


def _xattr_snapshot(path: Path) -> tuple[tuple[str, str], ...]:
    if not hasattr(os, "listxattr") or not hasattr(os, "getxattr"):
        return ()
    try:
        names = os.listxattr(path, follow_symlinks=False)
        return tuple(
            (name, _sha256(os.getxattr(path, name, follow_symlinks=False)))
            for name in sorted(names)
        )
    except OSError as error:
        return (("<xattrs-unavailable>", str(error.errno)),)


def _tree_snapshot(
    root: Path,
) -> tuple[
    tuple[str, int, int | None, tuple[tuple[str, str], ...], str | None],
    ...,
]:
    rows: list[
        tuple[str, int, int | None, tuple[tuple[str, str], ...], str | None]
    ] = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        relative = path.relative_to(root).as_posix()
        mode = stat.S_IMODE(metadata.st_mode)
        flags = getattr(metadata, "st_flags", None)
        xattrs = _xattr_snapshot(path)
        digest = _sha256(path.read_bytes()) if path.is_file() else None
        rows.append((relative, mode, flags, xattrs, digest))
    return tuple(rows)


def _assert_not_consumable(destination: Path) -> None:
    for child in (_payload(destination), _receipt_path(destination)):
        assert not child.is_file()
        assert not child.exists()
        assert not child.is_symlink()


def _inode(path: Path) -> tuple[int, int]:
    observed = path.lstat()
    return observed.st_dev, observed.st_ino


def _path_snapshot(
    path: Path,
) -> tuple[
    tuple[int, tuple[int, int], int, int, str | None],
    tuple[
        tuple[str, int, int | None, tuple[tuple[str, str], ...], str | None],
        ...,
    ],
]:
    return (
        _object_snapshot(path),
        _tree_snapshot(path) if path.is_dir() else (),
    )


def _hidden_staging_sibling(destination: Path) -> Path:
    candidates = [
        child
        for child in destination.parent.iterdir()
        if child != destination
        and child.name.startswith(".")
        and child.is_dir()
        and not child.is_symlink()
    ]
    assert len(candidates) == 1
    return candidates[0]


def _object_snapshot(
    path: Path,
) -> tuple[int, tuple[int, int], int, int, str | None]:
    observed = path.lstat()
    digest = _sha256(path.read_bytes()) if stat.S_ISREG(observed.st_mode) else None
    return (
        stat.S_IFMT(observed.st_mode),
        (observed.st_dev, observed.st_ino),
        observed.st_size,
        stat.S_IMODE(observed.st_mode),
        digest,
    )


def _assert_exact_publication_closure(
    directory: Path,
    expected_receipt: dict,
) -> None:
    directory_stat = directory.lstat()
    payload_path = _payload(directory)
    receipt_path = _receipt_path(directory)
    payload_stat = payload_path.lstat()
    receipt_stat = receipt_path.lstat()
    assert stat.S_ISDIR(directory_stat.st_mode)
    assert stat.S_IMODE(directory_stat.st_mode) == 0o700
    assert {child.name for child in directory.iterdir()} == {
        _PAYLOAD_NAME,
        _RECEIPT_NAME,
    }
    assert stat.S_ISREG(payload_stat.st_mode)
    assert stat.S_IMODE(payload_stat.st_mode) == 0o444
    assert stat.S_ISREG(receipt_stat.st_mode)
    assert stat.S_IMODE(receipt_stat.st_mode) == 0o444
    assert payload_stat.st_nlink == 1
    assert receipt_stat.st_nlink == 1

    payload_raw = payload_path.read_bytes()
    receipt_raw = receipt_path.read_bytes()
    observed_receipt = json.loads(receipt_raw)
    assert observed_receipt == expected_receipt
    assert receipt_raw == _canonical(expected_receipt)
    assert observed_receipt["payload_size_bytes"] == len(payload_raw)
    assert observed_receipt["payload_sha256"] == _sha256(payload_raw)
    assert observed_receipt["payload_path"] == _PAYLOAD_NAME


def test_separate_process_builds_ignore_hash_seed_and_are_byte_identical(
    tmp_path: Path,
) -> None:
    destinations = [tmp_path / "seed-1", tmp_path / "seed-987654"]
    for seed, destination in zip(("1", "987654"), destinations, strict=True):
        subprocess.run(
            [
                sys.executable,
                "-c",
                _BUILD_PROCESS,
                str(destination),
                _COMMAND_ID,
                _REQUESTED_TARGET_RUN_ID,
                _RUNNER_SOURCE_SHA256,
                _RUNNER_TEST_SHA256,
            ],
            cwd=_REPO_ROOT,
            env={"PYTHONHASHSEED": seed},
            check=True,
            capture_output=True,
            text=True,
        )

    assert _payload(destinations[0]).read_bytes() == _payload(
        destinations[1]
    ).read_bytes()
    assert _receipt_path(destinations[0]).read_bytes() == _receipt_path(
        destinations[1]
    ).read_bytes()


def test_digest_closure_recomputes_every_manifest_member_payload_and_receipt_digest(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "payload"
    returned = _build(destination)
    receipt_raw = _receipt_path(destination).read_bytes()
    receipt = _receipt(destination)
    manifest, manifest_raw = _manifest(destination)
    payload_raw = _payload(destination).read_bytes()

    assert returned == receipt
    assert receipt_raw == _canonical(receipt)
    assert receipt["payload_sha256"] == _sha256(payload_raw)
    assert receipt["payload_size_bytes"] == len(payload_raw)
    assert receipt["payload_manifest_member"] == _MANIFEST_NAME
    assert receipt["payload_manifest_sha256"] == _sha256(manifest_raw)
    assert receipt["payload_manifest_size_bytes"] == len(manifest_raw)

    component_input_raw = _canonical(receipt["component_input"])
    assert receipt["component_input_sha256"] == _sha256(component_input_raw)
    assert manifest["component_input"] == receipt["component_input"]
    assert manifest["component_input_sha256"] == _sha256(component_input_raw)
    assert manifest["fixed_nonce_sha256"] == _FIXED_NONCE_SHA256
    assert receipt["fixed_nonce_sha256"] == _FIXED_NONCE_SHA256

    with zipfile.ZipFile(_payload(destination)) as archive:
        assert {row["path"] for row in manifest["members"]} == {
            "run.sh",
            "transport_smoke.py",
        }
        for row in manifest["members"]:
            raw = archive.read(row["path"])
            assert row["sha256"] == _sha256(raw)
            assert row["size_bytes"] == len(raw)
            assert row["mode"] == (
                "0500" if row["path"] == "run.sh" else "0400"
            )

    digest_keys = {
        "component_input_sha256",
        "fixed_nonce_sha256",
        "payload_manifest_sha256",
        "payload_sha256",
        "runner_source_sha256",
        "runner_test_sha256",
    }
    assert {key for key in receipt if key.endswith("_sha256")} == digest_keys
    assert {
        key for key in manifest if key.endswith("_sha256")
    } == digest_keys - {"payload_manifest_sha256", "payload_sha256"}

def test_shared_constructor_reproduces_the_exact_published_payload_closure(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "payload"
    _build(destination)
    receipt = _receipt(destination)
    payload_raw, manifest_raw = construct_transport_smoke_payload(
        receipt["component_input"]
    )

    assert payload_raw == _payload(destination).read_bytes()
    assert manifest_raw == _manifest(destination)[1]
    assert receipt["payload_sha256"] == _sha256(payload_raw)
    assert receipt["payload_size_bytes"] == len(payload_raw)
    assert receipt["payload_manifest_sha256"] == _sha256(manifest_raw)
    assert receipt["payload_manifest_size_bytes"] == len(manifest_raw)


def test_each_bound_input_changes_component_manifest_payload_and_receipt(
    tmp_path: Path,
) -> None:
    variants = [
        {},
        {"runner_source_sha256": "sha256:" + "3" * 64},
        {"runner_test_sha256": "sha256:" + "4" * 64},
        {"command_id": "transport-smoke-r2"},
        {
            "requested_target_run_id": (
                "20260716T130000Z-slurm-pending"
            )
        },
    ]
    observations: list[tuple[str, str, str, bytes]] = []
    for index, overrides in enumerate(variants):
        destination = tmp_path / f"variant-{index}"
        _build(destination, **overrides)
        receipt = _receipt(destination)
        observations.append(
            (
                receipt["component_input_sha256"],
                receipt["payload_manifest_sha256"],
                receipt["payload_sha256"],
                _receipt_path(destination).read_bytes(),
            )
        )

    for field in range(4):
        assert len({row[field] for row in observations}) == len(variants)


def test_zip_metadata_order_and_member_types_are_exact(tmp_path: Path) -> None:
    destination = tmp_path / "payload"
    _build(destination)

    with zipfile.ZipFile(_payload(destination)) as archive:
        infos = archive.infolist()
        assert archive.comment == b""
        assert [info.filename for info in infos] == [
            "run.sh",
            "transport_smoke.py",
            _MANIFEST_NAME,
        ]
        assert [stat.S_IMODE(info.external_attr >> 16) for info in infos] == [
            0o500,
            0o400,
            0o400,
        ]
        for info in infos:
            archived_mode = info.external_attr >> 16
            assert stat.S_IFMT(archived_mode) == stat.S_IFREG
            assert info.date_time == (1980, 1, 1, 0, 0, 0)
            assert info.compress_type == zipfile.ZIP_STORED
            assert info.create_system == 3
            assert info.extra == b""
            assert info.comment == b""


def test_manifest_receipt_identity_execution_contract_and_modes_are_exact(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "payload"
    _build(destination)
    manifest, _ = _manifest(destination)
    receipt = _receipt(destination)
    assert set(receipt) == {
        "admission_binding",
        "admission_revalidation_required",
        "campaign_admission",
        "claim_boundary",
        "command_id",
        "component_identity",
        "component_input",
        "component_input_sha256",
        "deterministic_double_build",
        "fixed_nonce_sha256",
        "incomplete_without_receipt",
        "passed",
        "payload_manifest_member",
        "payload_manifest_sha256",
        "payload_manifest_size_bytes",
        "payload_path",
        "payload_sha256",
        "payload_size_bytes",
        "publication_guarantee",
        "publication_state",
        "requested_target_run_id",
        "runner_source_sha256",
        "runner_test_sha256",
        "same_uid_mutation_exclusion",
        "schema_version",
        "target_execution",
        "transport_authority",
    }

    component_input = {
        "command_id": _COMMAND_ID,
        "fixed_nonce_sha256": _FIXED_NONCE_SHA256,
        "requested_target_run_id": _REQUESTED_TARGET_RUN_ID,
        "runner_source_sha256": _RUNNER_SOURCE_SHA256,
        "runner_test_sha256": _RUNNER_TEST_SHA256,
    }
    assert manifest["component_input"] == component_input
    assert receipt["component_input"] == component_input
    assert manifest["command_id"] == receipt["command_id"] == _COMMAND_ID
    assert (
        manifest["requested_target_run_id"]
        == receipt["requested_target_run_id"]
        == _REQUESTED_TARGET_RUN_ID
    )
    assert manifest["runner_source_sha256"] == _RUNNER_SOURCE_SHA256
    assert manifest["runner_test_sha256"] == _RUNNER_TEST_SHA256
    assert manifest["component"] == "transport_smoke"
    assert manifest["report_id"] == "transport-smoke-fixed-v1"
    assert manifest["report_schema_version"] == "bb.rl.phase3.transport_smoke.v1"
    assert manifest["resources"] == {
        "deadline_seconds": 300,
        "gpus": 0,
        "nodes": 1,
        "tasks": 1,
    }
    assert manifest["execution_contract"] == {
        "argv": [],
        "minimum_python_version": "3.8.0",
        "python_bytecode_writes": False,
        "required_executables": ["/bin/bash", "/usr/bin/python3"],
    }
    assert manifest["nonclaims"] == _NONCLAIMS

    assert receipt["publication_state"] == "complete"
    assert receipt["incomplete_without_receipt"] is True
    assert receipt["transport_authority"] is False
    assert receipt["target_execution"] is False
    assert receipt["campaign_admission"] is False
    assert receipt["claim_boundary"] == (
        "local_deterministic_build_and_cooperative_atomic_visibility_only"
    )
    assert receipt["publication_guarantee"] == "atomic_visibility_only"
    assert receipt["same_uid_mutation_exclusion"] is False
    assert receipt["admission_revalidation_required"] is True
    assert receipt["admission_binding"] == (
        "authority_admission_sha256_equals_canonical_receipt_sha256"
    )
    assert stat.S_IMODE(destination.stat().st_mode) == 0o700
    assert stat.S_IMODE(_payload(destination).stat().st_mode) == 0o444
    assert stat.S_IMODE(_receipt_path(destination).stat().st_mode) == 0o444


def test_declared_absolute_executables_exist_and_python_contract_is_compatible(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "payload"
    _build(destination)
    manifest, _ = _manifest(destination)
    contract = manifest["execution_contract"]

    for executable in contract["required_executables"]:
        path = Path(executable)
        assert path.is_absolute()
        assert path.is_file()
        assert os.access(path, os.X_OK)
    bash = subprocess.run(
        ["/bin/bash", "--noprofile", "--norc", "-c", "exit 0"],
        env={},
        check=False,
        capture_output=True,
        text=True,
    )
    assert bash.returncode == 0
    assert bash.stdout == bash.stderr == ""
    python = subprocess.run(
        [
            "/usr/bin/python3",
            "-B",
            "-c",
            "import sys;print('.'.join(map(str,sys.version_info[:3])))",
        ],
        env={"PYTHONDONTWRITEBYTECODE": "1"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert python.returncode == 0
    observed = tuple(int(part) for part in python.stdout.strip().split("."))
    minimum = tuple(
        int(part) for part in contract["minimum_python_version"].split(".")
    )
    assert observed >= minimum
    assert python.stderr == ""


def test_fixed_nonce_stdout_report_and_raw_nonclaim_boundary_are_exact(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "payload"
    extracted = tmp_path / "extracted"
    unrelated = tmp_path / "unrelated-cwd"
    unrelated.mkdir()
    _build(destination)
    manifest, _ = _manifest(destination)
    run_sh = _extract(destination, extracted)
    environment = _valid_environment(destination)

    completed = _run_smoke(
        run_sh, environment=environment, cwd=unrelated
    )
    report = _report_from_success(completed)

    expected_identity = {
        "command_id": _COMMAND_ID,
        "requested_target_run_id": _REQUESTED_TARGET_RUN_ID,
        "runner_source_sha256": _RUNNER_SOURCE_SHA256,
        "runner_test_sha256": _RUNNER_TEST_SHA256,
        "target_run_id": _FINAL_TARGET_RUN_ID,
    }
    observed_identity = {
        "command_id": _COMMAND_ID,
        "payload_zip_sha256": environment["PHASE3_PAYLOAD_ZIP_SHA256"],
        "phase3_slurm_job_id": _JOB_ID,
        "slurm_job_id": _JOB_ID,
        "target_run_id": _FINAL_TARGET_RUN_ID,
    }
    node_observation = {
        "slurm_nnodes": 1,
        "slurm_ntasks": 1,
        "slurmd_nodename": _NODE_NAME,
    }
    observation_output = {
        "component_input_sha256": manifest["component_input_sha256"],
        "node_observation": node_observation,
        "nonce_sha256": _FIXED_NONCE_SHA256,
        "observed_identity": observed_identity,
    }
    assert set(report) == {
        "authoritative",
        "claim_boundary",
        "component",
        "command_id",
        "component_input_digest",
        "payload_zip_sha256",
        "slurm_job_id",
        "target_run_id",
        "expected_identity",
        "node_observation",
        "nonclaims",
        "nonce_sha256",
        "observation_kind",
        "observation_output",
        "observation_output_sha256",
        "observed_identity",
        "outer_runner_authority_required",
        "passed",
        "report_id",
        "runtime",
        "schema_version",
    }
    assert report["schema_version"] == "bb.rl.phase3.transport_smoke.v1"
    assert report["component"] == "transport_smoke"
    assert report["report_id"] == "transport-smoke-fixed-v1"
    assert report["passed"] is True
    assert report["authoritative"] is False
    assert report["outer_runner_authority_required"] is True
    assert report["observation_kind"] == "raw_harmless_nonce_observation"
    assert report["claim_boundary"] == "raw_harmless_nonce_observation_only"
    assert report["nonclaims"] == _NONCLAIMS
    assert report["nonce_sha256"] == _FIXED_NONCE_SHA256
    assert manifest["fixed_nonce_sha256"] == _FIXED_NONCE_SHA256
    assert report["command_id"] == _COMMAND_ID
    assert report["component_input_digest"] == manifest["component_input_sha256"]
    assert report["payload_zip_sha256"] == environment["PHASE3_PAYLOAD_ZIP_SHA256"]
    assert report["slurm_job_id"] == _JOB_ID
    assert report["target_run_id"] == _FINAL_TARGET_RUN_ID
    assert report["expected_identity"] == expected_identity
    assert report["observed_identity"] == observed_identity
    assert report["node_observation"] == node_observation
    assert report["observation_output"] == observation_output
    assert report["observation_output_sha256"] == _sha256(
        _canonical(observation_output)
    )
    expected_python = subprocess.run(
        ["/usr/bin/python3", "-B", "-c", "import sys; print(sys.executable)"],
        cwd=unrelated,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert report["runtime"]["python_executable"] == expected_python
    assert set(report["runtime"]) == {
        "python_executable",
        "python_implementation",
        "python_version",
    }


def test_real_payload_report_is_accepted_by_the_actual_runner_parser(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "payload"
    extracted = tmp_path / "extracted"
    unrelated = tmp_path / "unrelated-cwd"
    unrelated.mkdir()
    _build(destination)
    manifest, _ = _manifest(destination)
    run_sh = _extract(destination, extracted)
    environment = _valid_environment(destination)

    completed = subprocess.run(
        [str(run_sh)],
        cwd=unrelated,
        env=environment,
        check=False,
        capture_output=True,
    )

    assert completed.returncode == 0
    reports, error = target_command_runner._parse_inline_reports(
        stdout=completed.stdout,
        stderr=completed.stderr,
        expected_reports=[
            {
                "component": "transport_smoke",
                "report_id": "transport-smoke-fixed-v1",
                "schema_version": "bb.rl.phase3.transport_smoke.v1",
                "component_input_digest": manifest["component_input_sha256"],
            }
        ],
        command_id=_COMMAND_ID,
        target_run_id=_FINAL_TARGET_RUN_ID,
        payload_sha256=environment["PHASE3_PAYLOAD_ZIP_SHA256"],
        job_id=_JOB_ID,
    )

    assert error == ""
    assert len(reports) == 1
    assert reports[0]["claim_boundary"] == "raw_harmless_nonce_observation_only"
    assert reports[0]["nonclaims"] == _NONCLAIMS


def test_absolute_run_path_works_from_unrelated_cwd_with_exact_minimal_environment(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "payload"
    extracted = tmp_path / "extracted"
    unrelated = tmp_path / "elsewhere"
    unrelated.mkdir()
    _build(destination)
    run_sh = _extract(destination, extracted).resolve()
    environment = _valid_environment(destination)

    assert set(environment) == set(_RUNTIME_ENV_KEYS)
    assert run_sh.is_absolute()
    completed = _run_smoke(
        run_sh, environment=environment, cwd=unrelated
    )
    _report_from_success(completed)
    assert not (unrelated / "transport_smoke.py").exists()


@pytest.mark.parametrize("key", _RUNTIME_ENV_KEYS)
@pytest.mark.parametrize("replacement", [None, ""])
def test_missing_or_empty_required_environment_never_emits_a_report(
    tmp_path: Path, key: str, replacement: str | None
) -> None:
    destination = tmp_path / "payload"
    extracted = tmp_path / "extracted"
    unrelated = tmp_path / "cwd"
    unrelated.mkdir()
    _build(destination)
    run_sh = _extract(destination, extracted)
    environment = _valid_environment(destination)
    if replacement is None:
        del environment[key]
    else:
        environment[key] = replacement

    completed = _run_smoke(
        run_sh, environment=environment, cwd=unrelated
    )
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert "PHASE3_COMPONENT_REPORT_JSON=" not in completed.stderr


@pytest.mark.parametrize(
    ("updates", "error_fragment"),
    [
        ({"PHASE3_COMMAND_ID": "other-command"}, "PHASE3_COMMAND_ID mismatch"),
        ({"PHASE3_COMMAND_ID": "bad command"}, "invalid PHASE3_COMMAND_ID"),
        (
            {"PHASE3_TARGET_RUN_ID": _REQUESTED_TARGET_RUN_ID},
            "PHASE3_TARGET_RUN_ID mismatch",
        ),
        ({"PHASE3_TARGET_RUN_ID": _COMMAND_ID}, "PHASE3_TARGET_RUN_ID mismatch"),
        (
            {"PHASE3_TARGET_RUN_ID": "20260716T120000Z-slurm-99999"},
            "PHASE3_TARGET_RUN_ID mismatch",
        ),
        ({"PHASE3_TARGET_RUN_ID": "bad target"}, "invalid PHASE3_TARGET_RUN_ID"),
        ({"PHASE3_SLURM_JOB_ID": "pending"}, "job IDs must match"),
        ({"PHASE3_SLURM_JOB_ID": "１２３４５"}, "job IDs must match"),
        ({"SLURM_JOB_ID": "+12345"}, "job IDs must match"),
        ({"SLURM_JOB_ID": "99999"}, "job IDs must match"),
        (
            {
                "PHASE3_SLURM_JOB_ID": "99999",
                "SLURM_JOB_ID": "99999",
            },
            "PHASE3_TARGET_RUN_ID mismatch",
        ),
        ({"PHASE3_PAYLOAD_ZIP_SHA256": "sha256:bad"}, "invalid PHASE3_PAYLOAD"),
        (
            {"PHASE3_PAYLOAD_ZIP_SHA256": "SHA256:" + "3" * 64},
            "invalid PHASE3_PAYLOAD",
        ),
        ({"SLURM_NNODES": "0"}, "SLURM_NNODES must equal 1"),
        ({"SLURM_NNODES": "01"}, "SLURM_NNODES must equal 1"),
        ({"SLURM_NTASKS": "2"}, "SLURM_NTASKS must equal 1"),
        ({"SLURM_NTASKS": "1.0"}, "SLURM_NTASKS must equal 1"),
        ({"SLURMD_NODENAME": "   "}, "invalid SLURMD_NODENAME"),
        ({"SLURMD_NODENAME": "node a01"}, "invalid SLURMD_NODENAME"),
        ({"SLURMD_NODENAME": "node\na01"}, "invalid SLURMD_NODENAME"),
        ({"SLURMD_NODENAME": "node\ta01"}, "invalid SLURMD_NODENAME"),
        ({"SLURMD_NODENAME": "n" * 257}, "invalid SLURMD_NODENAME"),
        ({"SLURMD_NODENAME": "-node"}, "invalid SLURMD_NODENAME"),
        ({"SLURMD_NODENAME": ".node"}, "invalid SLURMD_NODENAME"),
        ({"SLURMD_NODENAME": "_node"}, "invalid SLURMD_NODENAME"),
        ({"SLURMD_NODENAME": "../node"}, "invalid SLURMD_NODENAME"),
        ({"SLURMD_NODENAME": "node/01"}, "invalid SLURMD_NODENAME"),
        ({"SLURMD_NODENAME": "node:01"}, "invalid SLURMD_NODENAME"),
        ({"SLURMD_NODENAME": "node@01"}, "invalid SLURMD_NODENAME"),
    ],
)
def test_malformed_stale_swapped_or_wrong_runtime_bindings_are_rejected(
    tmp_path: Path,
    updates: dict[str, str],
    error_fragment: str,
) -> None:
    destination = tmp_path / "payload"
    extracted = tmp_path / "extracted"
    unrelated = tmp_path / "cwd"
    unrelated.mkdir()
    _build(destination)
    run_sh = _extract(destination, extracted)
    environment = _valid_environment(destination)
    environment.update(updates)

    completed = _run_smoke(
        run_sh, environment=environment, cwd=unrelated
    )
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert error_fragment in completed.stderr
    assert "PHASE3_COMPONENT_REPORT_JSON=" not in completed.stderr


def test_well_formed_payload_digest_is_explicitly_only_a_raw_outer_bound_observation(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "payload"
    extracted = tmp_path / "extracted"
    unrelated = tmp_path / "cwd"
    unrelated.mkdir()
    _build(destination)
    run_sh = _extract(destination, extracted)
    environment = _valid_environment(destination)
    observed_digest = "sha256:" + "9" * 64
    assert observed_digest != environment["PHASE3_PAYLOAD_ZIP_SHA256"]
    environment["PHASE3_PAYLOAD_ZIP_SHA256"] = observed_digest

    report = _report_from_success(
        _run_smoke(run_sh, environment=environment, cwd=unrelated)
    )
    assert report["observed_identity"]["payload_zip_sha256"] == observed_digest
    assert report["authoritative"] is False
    assert report["outer_runner_authority_required"] is True
    assert report["claim_boundary"] == "raw_harmless_nonce_observation_only"
    assert "outer transport authority" in report["nonclaims"]


def test_extra_argv_cannot_emit_nonce_or_report(tmp_path: Path) -> None:
    destination = tmp_path / "payload"
    extracted = tmp_path / "extracted"
    unrelated = tmp_path / "cwd"
    unrelated.mkdir()
    _build(destination)
    run_sh = _extract(destination, extracted)

    completed = _run_smoke(
        run_sh,
        environment=_valid_environment(destination),
        cwd=unrelated,
        arguments=("unexpected",),
    )
    assert completed.returncode != 0
    assert completed.stdout == ""
    assert completed.stderr == ""


def test_secret_canaries_do_not_leak_and_execution_does_not_mutate_workspace(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "payload"
    extracted = tmp_path / "extracted"
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    sentinel = unrelated / "sentinel.txt"
    sentinel.write_text("unchanged")
    _build(destination)
    _extract(destination, extracted)
    program = extracted / "transport_smoke.py"
    environment = _valid_environment(destination)
    canaries = {
        "AWS_SECRET_ACCESS_KEY": "secret-aws-canary-31f92d",
        "HF_TOKEN": "secret-hf-canary-7a00c1",
        "WANDB_API_KEY": "secret-wandb-canary-0bc913",
        "DATABASE_URL": "secret-db-canary-b10aa7",
    }
    environment.update(canaries)
    before_tree = _tree_snapshot(tmp_path)

    completed, denial_log = _run_smoke_with_effect_denials(
        program, environment=environment, cwd=unrelated
    )
    report = _report_from_success(completed)

    assert denial_log == []
    assert _tree_snapshot(tmp_path) == before_tree
    encoded_report = _canonical(report).decode()
    for secret in canaries.values():
        assert secret not in completed.stdout
        assert secret not in completed.stderr
        assert secret not in encoded_report
    assert not (extracted / "__pycache__").exists()
    assert sentinel.read_text() == "unchanged"


def test_embedded_program_import_and_effect_surface_is_allowlisted(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "payload"
    _build(destination)
    with zipfile.ZipFile(_payload(destination)) as archive:
        source = archive.read("transport_smoke.py").decode()

    imports, call_forms, violations = _embedded_effect_policy(source)
    assert imports == _ALLOWED_EMBEDDED_IMPORTS
    assert call_forms == _ALLOWED_EMBEDDED_CALL_FORMS
    assert violations == set()


@pytest.mark.parametrize(
    ("mutant", "expected_violation"),
    [
        (
            "open('/some/repository/path', 'w').write('mutated')",
            "call-form:open",
        ),
        (
            "getattr(os, 'system')('touch /tmp/mutated')",
            "call-form:getattr",
        ),
        (
            "__import__('socket').create_connection(('example.com', 443))",
            "call-form:__import__",
        ),
        ("eval(\"open('/tmp/mutated', 'w')\")", "call-form:eval"),
        ("exec(\"open('/tmp/mutated', 'w')\")", "call-form:exec"),
        (
            "compile(\"open('/tmp/mutated', 'w')\", '<mutant>', 'exec')",
            "call-form:compile",
        ),
        (
            "importlib.import_module('socket')",
            "call-form:.import_module",
        ),
        (
            "__builtins__['open']('/tmp/mutated', 'w')",
            "call-form:<dynamic>",
        ),
        (
            "os.setxattr(__file__, 'user.breadboard_mutant', b'x')",
            "call-form:os.setxattr",
        ),
        (
            "os.removexattr(__file__, 'user.breadboard_mutant')",
            "call-form:os.removexattr",
        ),
        (
            "getattr(os, 'kill')(os.getpid(), 0)",
            "call-form:getattr",
        ),
    ],
)
def test_embedded_effect_policy_rejects_plausible_escape_mutants(
    mutant: str,
    expected_violation: str,
) -> None:
    _, _, violations = _embedded_effect_policy(mutant)
    assert expected_violation in violations


@pytest.mark.parametrize(
    ("effect", "mutant"),
    [
        pytest.param(
            "setxattr",
            "import os\n"
            "os.setxattr(__file__, 'user.breadboard_mutant', b'x')\n",
            marks=pytest.mark.skipif(
                not hasattr(os, "setxattr"),
                reason="os.setxattr is unavailable",
            ),
        ),
        pytest.param(
            "removexattr",
            "import os\n"
            "os.removexattr(__file__, 'user.breadboard_mutant')\n",
            marks=pytest.mark.skipif(
                not hasattr(os, "removexattr"),
                reason="os.removexattr is unavailable",
            ),
        ),
        pytest.param(
            "chflags",
            "import os\n"
            "os.chflags(__file__, os.stat(__file__).st_flags)\n",
            marks=pytest.mark.skipif(
                not hasattr(os, "chflags"),
                reason="os.chflags is unavailable",
            ),
        ),
        pytest.param(
            "kill",
            "import os\ngetattr(os, 'kill')(os.getpid(), 0)\n",
            marks=pytest.mark.skipif(
                not hasattr(os, "kill"),
                reason="os.kill is unavailable",
            ),
        ),
        pytest.param(
            "killpg",
            "import os\ngetattr(os, 'killpg')(os.getpgrp(), 0)\n",
            marks=pytest.mark.skipif(
                not hasattr(os, "killpg"),
                reason="os.killpg is unavailable",
            ),
        ),
    ],
)
def test_runtime_harness_denies_metadata_and_process_control_mutants(
    tmp_path: Path,
    effect: str,
    mutant: str,
) -> None:
    program = tmp_path / ("mutant-" + effect + ".py")
    program.write_text(mutant)
    before = _tree_snapshot(tmp_path)

    completed, denial_log = _run_smoke_with_effect_denials(
        program,
        environment={},
        cwd=tmp_path,
    )

    assert completed.returncode != 0
    assert denial_log == ["os." + effect]
    assert _tree_snapshot(tmp_path) == before


def test_modes_are_applied_before_each_file_or_directory_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "payload"
    parent_inode = _inode(tmp_path)
    events: list[tuple[str, str | int, int, int, int]] = []
    real_fchmod = payload_builder.os.fchmod
    real_fsync = payload_builder._fsync

    def recording_fchmod(fd: int, mode: int) -> None:
        real_fchmod(fd, mode)
        observed = os.fstat(fd)
        events.append(
            (
                "fchmod",
                mode,
                observed.st_dev,
                observed.st_ino,
                stat.S_IMODE(observed.st_mode),
            )
        )

    def recording_fsync(fd: int, stage: str) -> None:
        observed = os.fstat(fd)
        events.append(
            (
                "fsync",
                stage,
                observed.st_dev,
                observed.st_ino,
                stat.S_IMODE(observed.st_mode),
            )
        )
        real_fsync(fd, stage)

    monkeypatch.setattr(payload_builder.os, "fchmod", recording_fchmod)
    monkeypatch.setattr(payload_builder, "_fsync", recording_fsync)
    _build(destination)
    published_staging_inode = _inode(destination)

    stages = [event[1] for event in events if event[0] == "fsync"]
    assert stages == [
        "staging_directory",
        "staging_parent",
        "staging_payload_file",
        "staging_receipt_file",
        "staging_tuple_durable",
        "committed_parent",
    ]
    fsync_inode_by_stage = {
        event[1]: (event[2], event[3])
        for event in events
        if event[0] == "fsync"
    }
    assert published_staging_inode != parent_inode
    assert fsync_inode_by_stage["staging_directory"] == published_staging_inode
    assert fsync_inode_by_stage["staging_tuple_durable"] == published_staging_inode
    assert fsync_inode_by_stage["staging_parent"] == parent_inode
    assert fsync_inode_by_stage["committed_parent"] == parent_inode
    chmod_position_by_inode = {
        (event[2], event[3]): index
        for index, event in enumerate(events)
        if event[0] == "fchmod"
    }
    for index, event in enumerate(events):
        inode = (event[2], event[3])
        if event[0] == "fsync" and inode in chmod_position_by_inode:
            assert chmod_position_by_inode[inode] < index

    mode_by_stage = {
        event[1]: event[4]
        for event in events
        if event[0] == "fsync"
        and event[1]
        in {
            "staging_directory",
            "staging_payload_file",
            "staging_receipt_file",
            "staging_tuple_durable",
        }
    }
    assert mode_by_stage == {
        "staging_directory": 0o700,
        "staging_payload_file": 0o444,
        "staging_receipt_file": 0o444,
        "staging_tuple_durable": 0o700,
    }


@pytest.mark.parametrize(
    "stage",
    [
        "staging_directory",
        "staging_parent",
        "staging_payload_file",
        "staging_receipt_file",
        "staging_tuple_durable",
    ],
)
def test_precommit_durability_failure_leaves_no_public_tuple(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    destination = tmp_path / "payload"
    real_fsync = payload_builder._fsync

    def failing_fsync(fd: int, observed_stage: str) -> None:
        if observed_stage == stage:
            raise _PrimaryPublicationError("primary fsync failure: " + stage)
        real_fsync(fd, observed_stage)

    monkeypatch.setattr(payload_builder, "_fsync", failing_fsync)
    with pytest.raises(
        _PrimaryPublicationError,
        match="primary fsync failure: " + stage,
    ):
        _build(destination)
    _assert_not_consumable(destination)


@pytest.mark.parametrize("operation", ["mkdir", "open_payload", "write", "fchmod"])
def test_publication_write_and_mode_failures_leave_no_public_tuple(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
) -> None:
    destination = tmp_path / "payload"
    message = "primary " + operation + " failure"
    if operation == "mkdir":
        def failing_mkdir(*args: object, **kwargs: object) -> None:
            raise _PrimaryPublicationError(message)

        monkeypatch.setattr(payload_builder.os, "mkdir", failing_mkdir)
    elif operation == "open_payload":
        real_open = payload_builder.os.open

        def failing_open(path: object, *args: object, **kwargs: object) -> int:
            if path == _PAYLOAD_NAME:
                raise _PrimaryPublicationError(message)
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(payload_builder.os, "open", failing_open)
    elif operation == "write":
        def failing_write(*args: object, **kwargs: object) -> int:
            raise _PrimaryPublicationError(message)

        monkeypatch.setattr(payload_builder.os, "write", failing_write)
    else:
        def failing_fchmod(*args: object, **kwargs: object) -> None:
            raise _PrimaryPublicationError(message)

        monkeypatch.setattr(payload_builder.os, "fchmod", failing_fchmod)

    with pytest.raises(_PrimaryPublicationError, match=message):
        _build(destination)
    _assert_not_consumable(destination)


@pytest.mark.parametrize(
    "stage",
    [
        "staging_directory",
        "staging_parent",
        "staging_payload_file",
        "staging_receipt_file",
        "staging_tuple_durable",
        "directory_committed",
        "committed_parent",
    ],
)
def test_real_process_crash_residue_is_hidden_or_an_exact_committed_tuple(
    tmp_path: Path,
    stage: str,
) -> None:
    destination = tmp_path / "payload"
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            _CRASH_BUILD_PROCESS,
            str(destination),
            stage,
            _COMMAND_ID,
            _REQUESTED_TARGET_RUN_ID,
            _RUNNER_SOURCE_SHA256,
            _RUNNER_TEST_SHA256,
        ],
        cwd=_REPO_ROOT,
        env={"PYTHONHASHSEED": "1"},
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 91
    assert completed.stdout == ""
    assert completed.stderr == ""
    hidden = [
        child
        for child in tmp_path.iterdir()
        if child.name.startswith(".") and child.is_dir()
    ]

    if stage in {"directory_committed", "committed_parent"}:
        assert hidden == []
        committed_receipt = _receipt(destination)
        _assert_exact_publication_closure(destination, committed_receipt)
        with pytest.raises(FileExistsError):
            _build(destination)
        _assert_exact_publication_closure(destination, committed_receipt)
    else:
        assert not destination.exists()
        assert len(hidden) == 1
        _assert_not_consumable(destination)
        retried = _build(destination)
        _assert_exact_publication_closure(destination, retried)
        assert hidden[0].exists()


@pytest.mark.parametrize(
    "stage",
    [
        "staging_payload_file",
        "staging_receipt_file",
        "staging_tuple_durable",
        "precommit_parent",
        "committed_parent",
    ],
)
def test_observer_sees_no_public_destination_then_complete_atomic_tuple(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
) -> None:
    destination = tmp_path / "payload"
    reached = threading.Event()
    release = threading.Event()
    results: list[dict] = []
    failures: list[BaseException] = []
    _install_publication_pause(
        monkeypatch,
        stage=stage,
        reached=reached,
        release=release,
    )

    def build() -> None:
        try:
            results.append(_build(destination))
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=build, daemon=True)
    thread.start()
    assert reached.wait(timeout=10), "publication stage was not reached: " + stage

    if stage == "committed_parent":
        observed_receipt = _receipt(destination)
        _assert_exact_publication_closure(destination, observed_receipt)
        assert all(
            not child.name.startswith(".")
            for child in destination.parent.iterdir()
            if child != destination
        )
    else:
        assert not destination.exists()
        _assert_not_consumable(destination)
        staging = _hidden_staging_sibling(destination)
        assert _payload(staging).is_file()
        if stage == "staging_payload_file":
            assert not _receipt_path(staging).exists()
        else:
            observed_receipt = _receipt(staging)
            _assert_exact_publication_closure(staging, observed_receipt)

    release.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    assert failures == []
    assert len(results) == 1
    _assert_exact_publication_closure(destination, results[0])


def test_one_atomic_noreplace_rename_is_the_complete_tuple_linearization_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "payload"
    real_rename = payload_builder._rename_directory_noreplace_at
    observations: list[tuple[tuple[int, int], dict]] = []

    def recording_rename(
        parent_fd: int,
        staging_leaf: str,
        destination_leaf: str,
    ) -> None:
        assert destination_leaf == destination.name
        staging = destination.parent / staging_leaf
        assert staging.name.startswith(".")
        assert staging != destination
        assert not destination.exists()
        staging_inode = _inode(staging)
        expected_receipt = _receipt(staging)
        _assert_exact_publication_closure(staging, expected_receipt)

        real_rename(parent_fd, staging_leaf, destination_leaf)

        assert not staging.exists()
        assert not staging.is_symlink()
        assert _inode(destination) == staging_inode
        _assert_exact_publication_closure(destination, expected_receipt)
        observations.append((staging_inode, expected_receipt))

    monkeypatch.setattr(
        payload_builder,
        "_rename_directory_noreplace_at",
        recording_rename,
    )
    result = _build(destination)

    assert len(observations) == 1
    staging_inode, expected_receipt = observations[0]
    assert _inode(destination) == staging_inode
    assert result == expected_receipt
    _assert_exact_publication_closure(destination, result)


@pytest.mark.parametrize(
    "checkpoint",
    [
        "precommit_staging_receipt",
        "precommit_staging_payload",
        "precommit_staging_directory",
        "precommit_parent",
    ],
)
def test_mutation_after_each_final_precommit_check_fails_without_public_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    checkpoint: str,
) -> None:
    canonical_parent = tmp_path / "canonical-parent"
    canonical_parent.mkdir()
    destination = canonical_parent / "payload"
    real_checkpoint = payload_builder._publication_checkpoint
    replacement_path: Path | None = None
    replacement_snapshot: object | None = None
    mutation_count = 0

    def mutating_checkpoint(observed_checkpoint: str) -> None:
        nonlocal mutation_count, replacement_path, replacement_snapshot
        real_checkpoint(observed_checkpoint)
        if observed_checkpoint != checkpoint:
            return
        mutation_count += 1
        assert not destination.exists()
        staging = _hidden_staging_sibling(destination)
        expected_receipt = _receipt(staging)
        _assert_exact_publication_closure(staging, expected_receipt)
        if checkpoint == "precommit_staging_receipt":
            replacement_path = _receipt_path(staging)
            replacement_path.unlink()
            replacement_path.write_bytes(b"unrelated receipt replacement")
            replacement_path.chmod(0o600)
        elif checkpoint == "precommit_staging_payload":
            replacement_path = _payload(staging)
            replacement_path.unlink()
            replacement_path.write_bytes(b"unrelated payload replacement")
            replacement_path.chmod(0o600)
        elif checkpoint == "precommit_staging_directory":
            moved = canonical_parent / ".owned-staging-moved"
            staging.rename(moved)
            staging.mkdir(mode=0o700)
            (staging / "unrelated").write_text("replacement staging directory")
            replacement_path = staging
        else:
            detached_parent = tmp_path / "detached-parent"
            canonical_parent.rename(detached_parent)
            canonical_parent.mkdir()
            (canonical_parent / "unrelated").write_text(
                "replacement canonical parent"
            )
            replacement_path = canonical_parent
        replacement_snapshot = _path_snapshot(replacement_path)

    monkeypatch.setattr(
        payload_builder,
        "_publication_checkpoint",
        mutating_checkpoint,
    )
    with pytest.raises((OSError, RuntimeError)) as captured:
        _build(destination)

    assert mutation_count == 1
    assert not isinstance(
        captured.value,
        payload_builder.PublicationRecoveryRequired,
    )
    assert replacement_path is not None
    assert replacement_snapshot is not None
    assert _path_snapshot(replacement_path) == replacement_snapshot
    _assert_not_consumable(destination)


def test_rename_response_loss_and_observation_failure_recover_by_staging_inode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "payload"
    primary = _PrimaryPublicationError("atomic no-replace rename response lost")
    observation = OSError("publication observation unavailable")
    real_rename = payload_builder._rename_directory_noreplace_at
    real_observe = payload_builder._observe_directory_publication
    rename_observations: list[tuple[tuple[int, int], dict]] = []

    def rename_then_raise(
        parent_fd: int,
        staging_leaf: str,
        destination_leaf: str,
    ) -> None:
        staging = destination.parent / staging_leaf
        staging_inode = _inode(staging)
        expected_receipt = _receipt(staging)
        assert not destination.exists()
        _assert_exact_publication_closure(staging, expected_receipt)
        real_rename(parent_fd, staging_leaf, destination_leaf)
        assert _inode(destination) == staging_inode
        _assert_exact_publication_closure(destination, expected_receipt)
        rename_observations.append((staging_inode, expected_receipt))
        raise primary

    monkeypatch.setattr(
        payload_builder,
        "_rename_directory_noreplace_at",
        rename_then_raise,
    )
    with pytest.raises(payload_builder.PublicationRecoveryRequired) as captured:
        _build(destination)
    recovery = captured.value
    monkeypatch.setattr(
        payload_builder,
        "_rename_directory_noreplace_at",
        real_rename,
    )

    assert len(rename_observations) == 1
    staging_inode, expected_receipt = rename_observations[0]
    assert recovery.stage == "atomic_directory_rename"
    assert recovery.__cause__ is primary
    assert recovery.primary_error is primary
    assert recovery.committed is None
    assert recovery.receipt_presence is True
    assert recovery.destination == destination
    assert recovery.staging.parent == destination.parent
    assert recovery.staging_inode == staging_inode
    assert _inode(destination) == recovery.staging_inode
    assert recovery.receipt_inode == _inode(_receipt_path(destination))
    assert recovery.payload_inode == _inode(_payload(destination))

    unrelated_staging = recovery.staging
    unrelated_staging.mkdir(mode=0o700)
    (unrelated_staging / "unrelated").write_text(
        "replacement at retired staging name"
    )
    unrelated_snapshot = _path_snapshot(unrelated_staging)

    def failing_observation(*args: object, **kwargs: object) -> bool | None:
        raise observation

    monkeypatch.setattr(
        payload_builder,
        "_observe_directory_publication",
        failing_observation,
    )
    with pytest.raises(payload_builder.PublicationRecoveryRequired) as retried:
        payload_builder.recover_publication(recovery)
    followup = retried.value
    assert followup is not recovery
    assert followup.__cause__ is observation
    assert followup.committed is None
    assert followup.receipt_presence is True
    assert followup.staging_inode == recovery.staging_inode
    assert _path_snapshot(unrelated_staging) == unrelated_snapshot

    monkeypatch.setattr(
        payload_builder,
        "_observe_directory_publication",
        real_observe,
    )
    recovered = payload_builder.recover_publication(followup)

    assert recovered == expected_receipt
    assert _inode(destination) == staging_inode
    _assert_exact_publication_closure(destination, recovered)
    assert _path_snapshot(unrelated_staging) == unrelated_snapshot


@pytest.mark.parametrize("child_name", [_PAYLOAD_NAME, _RECEIPT_NAME])
def test_postcommit_mutation_raises_committed_recovery_and_never_returns_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    child_name: str,
) -> None:
    destination = tmp_path / "payload"
    real_rename = payload_builder._rename_directory_noreplace_at
    committed_inode: tuple[int, int] | None = None
    replacement_snapshot: object | None = None

    def rename_then_mutate(
        parent_fd: int,
        staging_leaf: str,
        destination_leaf: str,
    ) -> None:
        nonlocal committed_inode, replacement_snapshot
        staging = destination.parent / staging_leaf
        expected_receipt = _receipt(staging)
        assert not destination.exists()
        _assert_exact_publication_closure(staging, expected_receipt)
        committed_inode = _inode(staging)

        real_rename(parent_fd, staging_leaf, destination_leaf)

        assert _inode(destination) == committed_inode
        _assert_exact_publication_closure(destination, expected_receipt)
        target = destination / child_name
        target.unlink()
        target.write_bytes(
            ("postcommit replacement:" + child_name).encode()
        )
        target.chmod(0o600)
        replacement_snapshot = _path_snapshot(target)

    monkeypatch.setattr(
        payload_builder,
        "_rename_directory_noreplace_at",
        rename_then_mutate,
    )
    with pytest.raises(payload_builder.PublicationRecoveryRequired) as captured:
        _build(destination)

    recovery = captured.value
    assert recovery.committed is True
    assert recovery.receipt_presence is True
    assert recovery.stage == "postcommit_closure"
    assert committed_inode is not None
    assert _inode(destination) == committed_inode
    target = destination / child_name
    assert replacement_snapshot is not None
    assert _path_snapshot(target) == replacement_snapshot
    with pytest.raises(payload_builder.PublicationRecoveryRequired) as retried:
        payload_builder.recover_publication(recovery)
    assert retried.value.committed is True
    assert _path_snapshot(target) == replacement_snapshot


def test_missing_postcommit_receipt_reports_observed_absence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "payload"
    real_rename = payload_builder._rename_directory_noreplace_at

    def rename_then_remove_receipt(
        parent_fd: int,
        staging_leaf: str,
        destination_leaf: str,
    ) -> None:
        real_rename(parent_fd, staging_leaf, destination_leaf)
        (destination / _RECEIPT_NAME).unlink()

    monkeypatch.setattr(
        payload_builder,
        "_rename_directory_noreplace_at",
        rename_then_remove_receipt,
    )
    with pytest.raises(payload_builder.PublicationRecoveryRequired) as captured:
        _build(destination)

    recovery = captured.value
    assert recovery.committed is True
    assert recovery.receipt_presence is False
    assert destination.is_dir()
    assert not (destination / _RECEIPT_NAME).exists()
    with pytest.raises(payload_builder.PublicationRecoveryRequired) as retried:
        payload_builder.recover_publication(recovery)
    assert retried.value.receipt_presence is False


def test_moved_postcommit_directory_reports_receipt_absence_without_cleanup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    destination = tmp_path / "payload"
    moved = tmp_path / "moved-committed-payload"
    real_rename = payload_builder._rename_directory_noreplace_at

    def rename_then_move_directory(
        parent_fd: int,
        staging_leaf: str,
        destination_leaf: str,
    ) -> None:
        real_rename(parent_fd, staging_leaf, destination_leaf)
        destination.rename(moved)

    monkeypatch.setattr(
        payload_builder,
        "_rename_directory_noreplace_at",
        rename_then_move_directory,
    )
    with pytest.raises(payload_builder.PublicationRecoveryRequired) as captured:
        _build(destination)

    recovery = captured.value
    moved_snapshot = _tree_snapshot(moved)
    assert recovery.committed is True
    assert recovery.receipt_presence is False
    assert not destination.exists()
    with pytest.raises(payload_builder.PublicationRecoveryRequired) as retried:
        payload_builder.recover_publication(recovery)
    assert retried.value.receipt_presence is False
    assert _tree_snapshot(moved) == moved_snapshot


@pytest.mark.parametrize(
    ("child_name", "checkpoint"),
    [
        (_PAYLOAD_NAME, "precommit_staging_payload"),
        (_RECEIPT_NAME, "precommit_staging_receipt"),
    ],
)
def test_precommit_hardlink_race_fails_and_preserves_the_external_link(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    child_name: str,
    checkpoint: str,
) -> None:
    destination = tmp_path / "payload"
    external = tmp_path / ("external-" + child_name)
    real_checkpoint = payload_builder._publication_checkpoint

    def hardlinking_checkpoint(stage: str) -> None:
        real_checkpoint(stage)
        if stage == checkpoint:
            staging = _hidden_staging_sibling(destination)
            os.link(staging / child_name, external)

    monkeypatch.setattr(
        payload_builder,
        "_publication_checkpoint",
        hardlinking_checkpoint,
    )
    with pytest.raises(RuntimeError, match="published file"):
        _build(destination)

    assert not destination.exists()
    assert external.is_file()
    assert external.stat().st_nlink == 1
    assert external.read_bytes()


@pytest.mark.parametrize("child_name", [_PAYLOAD_NAME, _RECEIPT_NAME])
def test_postcommit_hardlink_race_requires_committed_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    child_name: str,
) -> None:
    destination = tmp_path / "payload"
    external = tmp_path / ("external-postcommit-" + child_name)
    real_rename = payload_builder._rename_directory_noreplace_at

    def rename_then_link(
        parent_fd: int,
        staging_leaf: str,
        destination_leaf: str,
    ) -> None:
        real_rename(parent_fd, staging_leaf, destination_leaf)
        os.link(destination / child_name, external)

    monkeypatch.setattr(
        payload_builder,
        "_rename_directory_noreplace_at",
        rename_then_link,
    )
    with pytest.raises(payload_builder.PublicationRecoveryRequired) as captured:
        _build(destination)

    recovery = captured.value
    assert recovery.committed is True
    assert recovery.stage == "postcommit_closure"
    assert external.stat().st_nlink == 2
    with pytest.raises(payload_builder.PublicationRecoveryRequired) as retried:
        payload_builder.recover_publication(recovery)
    assert retried.value.committed is True
    assert external.stat().st_nlink == 2


def test_concurrent_builder_cannot_overwrite_a_committed_destination(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "payload"
    reached = threading.Event()
    release = threading.Event()
    results: list[dict] = []
    failures: list[BaseException] = []
    _install_publication_pause(
        monkeypatch,
        stage="committed_parent",
        reached=reached,
        release=release,
    )

    def first_build() -> None:
        try:
            results.append(_build(destination))
        except BaseException as error:
            failures.append(error)

    thread = threading.Thread(target=first_build, daemon=True)
    thread.start()
    try:
        assert reached.wait(timeout=10), "atomic commit was not reached"
        committed_receipt = _receipt(destination)
        _assert_exact_publication_closure(destination, committed_receipt)
        committed_snapshot = _path_snapshot(destination)

        with pytest.raises(FileExistsError):
            _build(destination, command_id="transport-smoke-r2")

        assert _path_snapshot(destination) == committed_snapshot
        _assert_exact_publication_closure(destination, committed_receipt)
    finally:
        release.set()
        thread.join(timeout=10)

    assert not thread.is_alive()
    assert failures == []
    assert results == [committed_receipt]
    _assert_exact_publication_closure(destination, committed_receipt)


def test_dangling_and_final_symlink_destinations_are_never_followed(
    tmp_path: Path,
) -> None:
    dangling_target = tmp_path / "missing-target"
    dangling = tmp_path / "dangling"
    dangling.symlink_to(dangling_target, target_is_directory=True)
    final_target = tmp_path / "final-target"
    final_target.mkdir()
    final = tmp_path / "final"
    final.symlink_to(final_target, target_is_directory=True)

    for destination in (dangling, final):
        with pytest.raises(FileExistsError):
            _build(destination)
        assert destination.is_symlink()
        _assert_not_consumable(destination)
    assert not dangling_target.exists()
    assert tuple(final_target.iterdir()) == ()


def test_empty_destination_created_at_final_syscall_is_preserved_unowned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "payload"
    real_rename = payload_builder._rename_directory_noreplace_at
    unrelated_snapshot: object | None = None

    def racing_rename(
        parent_fd: int,
        staging_leaf: str,
        destination_leaf: str,
    ) -> None:
        nonlocal unrelated_snapshot
        assert not destination.exists()
        destination.mkdir(mode=0o711)
        destination.chmod(0o711)
        unrelated_snapshot = _path_snapshot(destination)
        real_rename(parent_fd, staging_leaf, destination_leaf)

    monkeypatch.setattr(
        payload_builder,
        "_rename_directory_noreplace_at",
        racing_rename,
    )
    with pytest.raises(FileExistsError):
        _build(destination)

    assert unrelated_snapshot is not None
    assert _path_snapshot(destination) == unrelated_snapshot
    assert tuple(destination.iterdir()) == ()
    assert not [
        child
        for child in tmp_path.iterdir()
        if child.name.startswith(".") and child.is_dir()
    ]


def test_concurrent_publishers_have_exactly_one_winner_and_one_complete_receipt(
    tmp_path: Path,
) -> None:
    destination = tmp_path / "payload"
    gate = tmp_path / "start-gate"
    command = [
        sys.executable,
        "-c",
        _CONCURRENT_BUILD_PROCESS,
        str(destination),
        str(gate),
        _COMMAND_ID,
        _REQUESTED_TARGET_RUN_ID,
        _RUNNER_SOURCE_SHA256,
        _RUNNER_TEST_SHA256,
    ]
    processes = [
        subprocess.Popen(
            command,
            cwd=_REPO_ROOT,
            env={"PYTHONHASHSEED": seed},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        for seed in ("11", "29")
    ]
    gate.touch()
    results = [process.communicate(timeout=15) for process in processes]

    assert [process.returncode for process in processes] == [0, 0]
    assert sorted(stdout.strip() for stdout, _ in results) == ["lost", "published"]
    assert all(stderr == "" for _, stderr in results)
    receipt = _receipt(destination)
    assert receipt["publication_state"] == "complete"
    assert receipt["payload_sha256"] == _sha256(_payload(destination).read_bytes())


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("runner_source_sha256", "sha256:bad", "invalid runner source SHA-256"),
        ("runner_source_sha256", "SHA256:" + "a" * 64, "invalid runner source SHA-256"),
        ("runner_test_sha256", "sha256:" + "G" * 64, "invalid runner test SHA-256"),
        ("command_id", "", "invalid command_id"),
        ("command_id", "-starts-with-dash", "invalid command_id"),
        ("command_id", "contains space", "invalid command_id"),
        ("command_id", "x" * 129, "invalid command_id"),
        (
            "requested_target_run_id",
            "transport-smoke-final",
            "invalid requested_target_run_id",
        ),
        (
            "requested_target_run_id",
            "-slurm-pending",
            "invalid requested_target_run_id",
        ),
        (
            "requested_target_run_id",
            "x" * 115 + "-slurm-pending",
            "invalid requested_target_run_id",
        ),
    ],
)
def test_invalid_hashes_and_identifiers_are_rejected_before_publication(
    tmp_path: Path,
    field: str,
    value: str,
    message: str,
) -> None:
    destination = tmp_path / "payload"
    with pytest.raises(ValueError, match=message):
        _build(destination, **{field: value})
    assert not destination.exists()


@pytest.mark.parametrize("kind", ["directory", "file", "symlink"])
def test_every_existing_destination_kind_is_rejected_without_mutation(
    tmp_path: Path, kind: str
) -> None:
    destination = tmp_path / "existing"
    if kind == "directory":
        destination.mkdir()
        marker = destination / "marker"
        marker.write_text("unchanged")
    elif kind == "file":
        destination.write_text("unchanged")
    else:
        target = tmp_path / "target"
        target.mkdir()
        destination.symlink_to(target, target_is_directory=True)
    before = _tree_snapshot(tmp_path)

    with pytest.raises(FileExistsError):
        _build(destination)
    assert _tree_snapshot(tmp_path) == before


def test_destination_must_name_a_real_child(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="destination must name a child"):
        _build(tmp_path / "..")
