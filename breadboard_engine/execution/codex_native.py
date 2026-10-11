"""Shared, explicitly built pinned Codex native helpers, including installed wheels."""
from __future__ import annotations

import argparse
import hashlib
import os
from pathlib import Path
import platform
import subprocess
from typing import Literal

Helper = Literal["patch", "search", "image"]
HELPERS = ("patch", "search", "image")
SOURCE_ROOT = Path(__file__).resolve().parent


class NativeHelperUnavailable(ValueError):
    """An actionable native resource failure returned as a tool error."""


def missing_helper_message(helper: Helper) -> str:
    return (f"Codex {helper} helper is not built; run "
            f"`python -m breadboard_engine.execution.codex_native build {helper}`.")


def _validate_helper(helper: str) -> None:
    if helper not in HELPERS:
        raise ValueError(f"Unknown Codex helper: {helper}")


def workspace_target() -> Path:
    digest = hashlib.sha256()
    paths = ["Cargo.toml", "Cargo.lock"]
    for helper in HELPERS:
        paths.extend((f"codex_{helper}/Cargo.toml", f"codex_{helper}/main.rs"))
    for relative in paths:
        try:
            content = (SOURCE_ROOT / relative).read_bytes()
        except OSError as exc:
            raise NativeHelperUnavailable(
                f"Codex native sources are unavailable: {relative}. Install a wheel with "
                "the native sources or configure BREADBOARD_CODEX_<HELPER>_BINARY."
            ) from exc
        digest.update(relative.encode())
        digest.update(b"\0")
        digest.update(content)
        digest.update(b"\0")
    cache = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache / "breadboard" / "codex-native" / digest.hexdigest()


def native_binary(helper: Helper, *, binary_path: str | Path | None = None) -> Path:
    _validate_helper(helper)
    override = binary_path or os.environ.get(f"BREADBOARD_CODEX_{helper.upper()}_BINARY")
    if override:
        return Path(override).expanduser().resolve()
    name = f"breadboard-codex-{helper}"
    bundled = SOURCE_ROOT / "bin" / f"{platform.system().lower()}-{platform.machine().lower()}" / name
    if bundled.is_file():
        return bundled
    return workspace_target() / "release" / name


def require_native_binary(helper: Helper) -> Path:
    binary = native_binary(helper)
    if not binary.is_file():
        raise NativeHelperUnavailable(missing_helper_message(helper))
    return binary


def build_helpers(helper: Helper | Literal["all"]) -> list[Path]:
    if helper != "all":
        _validate_helper(helper)
    target = workspace_target()
    selected = HELPERS if helper == "all" else (helper,)
    command = ["cargo", "build", "--locked", "--release", "--manifest-path",
               str(SOURCE_ROOT / "Cargo.toml"), "--target-dir", str(target)]
    if helper == "all":
        command.append("--workspace")
    else:
        command.extend(("--package", f"breadboard-codex-{helper}"))
    subprocess.run(command, check=True)
    return [target / "release" / f"breadboard-codex-{name}" for name in selected]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("build", help="Build pinned helpers explicitly, never at tool runtime")
    build.add_argument("helper", choices=(*HELPERS, "all"))
    args = parser.parse_args()
    for binary in build_helpers(args.helper):
        print(binary)


if __name__ == "__main__":
    main()
