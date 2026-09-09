"""Build the supplied BreadBoard worker wheel into an offline OCI image.

This is maintainer tooling, not an author-facing transport or launch path.  The
build context contains only the prebuilt BreadBoard wheel and an explicit wheel
house.  Docker is required to have the pinned base image already available;
``--network=none`` and pip's ``--no-index`` prevent downloads.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Sequence


_SHA256 = r"[0-9a-f]{64}"
_PINNED_BASE = re.compile(rf"^[^\s@]+@sha256:{_SHA256}$")
_IMAGE_ID = re.compile(rf"^sha256:{_SHA256}$")
_PLATFORM = re.compile(
    r"^linux/(?:amd64|arm64|arm(?:/v[5-8])?|386|ppc64le|s390x)(?:/[a-z0-9_.-]+)?$"
)
_MAX_OUTPUT_BYTES = 64 * 1024
_DEFAULT_TIMEOUT_SECONDS = 900.0


@dataclass(frozen=True, slots=True)
class CommandRecord:
    """Bounded evidence for one Docker command."""

    argv: tuple[str, ...]
    exit_code: int | None
    timed_out: bool
    stdout: str
    stderr: str

    def as_dict(self) -> dict[str, object]:
        return {
            "argv": list(self.argv),
            "exit_code": self.exit_code,
            "stderr": self.stderr,
            "stdout": self.stdout,
            "timed_out": self.timed_out,
        }


@dataclass(frozen=True, slots=True)
class BuildReport:
    """The truthful result of one image build attempt."""

    ok: bool
    image_id: str | None
    archive: str | None
    error: str | None
    commands: tuple[CommandRecord, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "archive": self.archive,
            "commands": [command.as_dict() for command in self.commands],
            "error": self.error,
            "image_id": self.image_id,
            "ok": self.ok,
        }


def _bounded_text(value: bytes) -> str:
    if len(value) <= _MAX_OUTPUT_BYTES:
        return value.decode("utf-8", errors="replace")
    return value[:_MAX_OUTPUT_BYTES].decode("utf-8", errors="replace") + "\n[output truncated]"


def _run_docker(
    docker: str,
    args: Sequence[str],
    timeout_seconds: float,
) -> CommandRecord:
    argv = (docker, *args)
    try:
        completed = subprocess.run(
            list(argv),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout_seconds,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        stdout = error.stdout if isinstance(error.stdout, bytes) else b""
        stderr = error.stderr if isinstance(error.stderr, bytes) else b""
        return CommandRecord(argv, None, True, _bounded_text(stdout), _bounded_text(stderr))
    except OSError as error:
        return CommandRecord(argv, None, False, "", str(error))
    return CommandRecord(
        argv,
        completed.returncode,
        False,
        _bounded_text(completed.stdout),
        _bounded_text(completed.stderr),
    )


def _platform_architectures(platform: str) -> tuple[str, ...]:
    architecture = platform.split("/", 2)[1]
    return {
        "amd64": ("x86_64", "amd64"),
        "arm64": ("aarch64", "arm64"),
        "arm": ("armv7l", "armhf", "arm"),
        "386": ("i686", "i386", "386"),
        "ppc64le": ("ppc64le",),
        "s390x": ("s390x",),
    }[architecture]


def _wheel_platform_tags(path: Path) -> tuple[str, ...]:
    if path.suffix != ".whl":
        raise ValueError(f"wheelhouse entry is not a wheel: {path.name}")
    fields = path.name[:-4].split("-")
    if len(fields) < 5 or not all(fields[-3:]):
        raise ValueError(f"invalid wheel filename: {path.name}")
    return tuple(fields[-1].split("."))


def _validate_wheel(path: Path, platform: str) -> None:
    if not path.is_file() or path.is_symlink():
        raise ValueError(f"wheelhouse entry must be a regular wheel file: {path.name}")
    architectures = _platform_architectures(platform)
    for tag in _wheel_platform_tags(path):
        if tag == "any":
            continue
        if not tag.startswith(("linux_", "manylinux", "musllinux")):
            raise ValueError(
                f"wheel {path.name} is not a Linux wheel; refusing host-native extensions"
            )
        if not any(architecture in tag for architecture in architectures):
            raise ValueError(
                f"wheel {path.name} is not compatible with Docker platform {platform}"
            )


def _validate_inputs(
    breadboard_wheel: Path,
    wheelhouse: Path,
    base_image: str,
    platform: str,
    timeout_seconds: float,
) -> tuple[Path, tuple[Path, ...]]:
    if _PINNED_BASE.fullmatch(base_image) is None:
        raise ValueError("base image must be a registry reference pinned by @sha256 digest")
    if _PLATFORM.fullmatch(platform) is None:
        raise ValueError("platform must be an explicit supported Linux platform")
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")
    wheel = breadboard_wheel.resolve()
    if not wheel.is_file() or wheel.is_symlink() or wheel.suffix != ".whl":
        raise ValueError("breadboard wheel must be an existing regular .whl file")
    root = wheelhouse.resolve()
    if not root.is_dir() or root.is_symlink():
        raise ValueError("wheelhouse must be an existing regular directory")
    dependencies = tuple(sorted(root.iterdir(), key=lambda item: item.name))
    if not dependencies:
        raise ValueError("offline wheelhouse must contain the required dependency wheels")
    _validate_wheel(wheel, platform)
    for dependency in dependencies:
        _validate_wheel(dependency, platform)
    return wheel, dependencies


def _build_context(
    context: Path,
    breadboard_wheel: Path,
    dependencies: Sequence[Path],
    base_image: str,
) -> None:
    wheel_target = context / "breadboard.whl"
    shutil.copyfile(breadboard_wheel, wheel_target)
    wheelhouse_target = context / "wheelhouse"
    wheelhouse_target.mkdir()
    for dependency in dependencies:
        shutil.copyfile(dependency, wheelhouse_target / dependency.name)
    (context / "Dockerfile").write_text(
        "\n".join(
            (
                f"FROM {base_image}",
                "COPY breadboard.whl /opt/breadboard-wheel/",
                "COPY wheelhouse/ /opt/breadboard-wheelhouse/",
                "RUN python3 -m pip install --no-cache-dir --no-index --only-binary=:all: "
                "--find-links=/opt/breadboard-wheelhouse /opt/breadboard-wheel/breadboard.whl",
                "RUN rm -rf /opt/breadboard-wheel /opt/breadboard-wheelhouse",
                'ENTRYPOINT ["python3", "-I", "-m", "breadboard.modules.worker"]',
                "",
            )
        ),
        encoding="utf-8",
    )


def build_author_worker_image(
    breadboard_wheel: Path,
    wheelhouse: Path,
    *,
    base_image: str,
    platform: str,
    archive: Path | None = None,
    tag: str | None = None,
    docker: str = "docker",
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
) -> BuildReport:
    """Build an installed worker image and return its observed identity.

    Every failure is represented by ``ok=False`` and a diagnostic; no image ID
    is invented when Docker did not return one.  ``archive`` is produced by
    ``docker save`` only after a successful build and identity inspection.
    """
    commands: list[CommandRecord] = []
    image_id: str | None = None
    archive_path: str | None = None
    try:
        archive_path = str(archive.resolve()) if archive is not None else None
        wheel, dependencies = _validate_inputs(
            breadboard_wheel, wheelhouse, base_image, platform, timeout_seconds
        )
        image_tag = tag or (
            "breadboard-author-worker:build-"
            + hashlib.sha256(wheel.read_bytes()).hexdigest()[:16]
        )
        if not image_tag or any(character.isspace() for character in image_tag) or "@" in image_tag:
            raise ValueError("tag must be a non-empty local image tag, not a digest reference")
        with tempfile.TemporaryDirectory(prefix="bb-author-worker-") as temporary:
            context = Path(temporary)
            _build_context(context, wheel, dependencies, base_image)
            built = _run_docker(
                docker,
                [
                    "build",
                    "--pull=false",
                    "--network=none",
                    "--platform",
                    platform,
                    "--tag",
                    image_tag,
                    "--file",
                    str(context / "Dockerfile"),
                    str(context),
                ],
                timeout_seconds,
            )
            commands.append(built)
            if built.exit_code != 0:
                return BuildReport(False, None, archive_path, "Docker image build failed", tuple(commands))
            inspected = _run_docker(
                docker,
                ["image", "inspect", "--format", "{{.Id}}", image_tag],
                timeout_seconds,
            )
            commands.append(inspected)
            observed = inspected.stdout.strip()
            if inspected.exit_code != 0:
                return BuildReport(False, None, archive_path, "Docker image identity inspection failed", tuple(commands))
            if _IMAGE_ID.fullmatch(observed) is None:
                return BuildReport(False, None, archive_path, "Docker returned an invalid image identity", tuple(commands))
            image_id = observed
            if archive is not None:
                archive.parent.mkdir(parents=True, exist_ok=True)
                saved = _run_docker(
                    docker,
                    ["save", "--output", str(archive), image_tag],
                    timeout_seconds,
                )
                commands.append(saved)
                if saved.exit_code != 0:
                    return BuildReport(False, image_id, archive_path, "Docker image archive export failed", tuple(commands))
                if not archive.is_file() or archive.stat().st_size == 0:
                    return BuildReport(False, image_id, archive_path, "Docker produced no loadable image archive", tuple(commands))
        return BuildReport(True, image_id, archive_path, None, tuple(commands))
    except (OSError, ValueError) as error:
        return BuildReport(False, image_id, archive_path, str(error), tuple(commands))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--breadboard-wheel", type=Path, required=True)
    parser.add_argument("--wheelhouse", type=Path, required=True)
    parser.add_argument("--base-image", required=True)
    parser.add_argument("--platform", required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--tag")
    parser.add_argument("--docker", default="docker")
    parser.add_argument("--timeout-seconds", type=float, default=_DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--report", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = _parser().parse_args(argv)
    report = build_author_worker_image(
        arguments.breadboard_wheel,
        arguments.wheelhouse,
        base_image=arguments.base_image,
        platform=arguments.platform,
        archive=arguments.archive,
        tag=arguments.tag,
        docker=arguments.docker,
        timeout_seconds=arguments.timeout_seconds,
    )
    encoded = json.dumps(report.as_dict(), sort_keys=True, separators=(",", ":"))
    if arguments.report is not None:
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        arguments.report.write_text(encoded + "\n", encoding="utf-8")
    print(encoded)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
