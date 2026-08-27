from __future__ import annotations

import os
import shutil
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]


def _clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PIP_NO_INDEX"] = "1"
    environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1"
    environment["UV_OFFLINE"] = "1"
    return environment


def test_python_sdk_types_import_from_wheel_and_reference_docs_stay_unpacked(
    tmp_path: Path,
) -> None:
    dist = tmp_path / "dist"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            "--wheel",
            "--no-isolation",
            "-C--build-option=build",
            f"-C--build-option=--build-base={tmp_path / 'wheel-build'}",
            "--outdir",
            str(dist),
            str(ROOT),
        ],
        cwd=tmp_path,
        env=_clean_environment(),
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(dist.glob("*.whl"))
    assert len(wheels) == 1
    wheel = wheels[0]

    with ZipFile(wheel) as archive:
        names = set(archive.namelist())
    assert "breadboard_sdk/types.py" in names
    assert not any(name.startswith("docs/reference/public/") for name in names)

    site = tmp_path / "site"
    uv = shutil.which("uv")
    assert uv is not None
    subprocess.run(
        [
            uv,
            "pip",
            "install",
            "--no-deps",
            "--offline",
            "--target",
            str(site),
            str(wheel),
        ],
        cwd=tmp_path,
        env=_clean_environment(),
        check=True,
        capture_output=True,
        text=True,
    )
    environment = _clean_environment()
    environment["PYTHONPATH"] = str(site)
    probe = """
import breadboard_sdk.types as types
from breadboard_sdk.types import Problem, PublicResult
from pathlib import Path
assert Path(types.__file__).resolve().is_relative_to(Path.cwd() / "site")
assert not hasattr(types, "NotRequired")
assert Problem.__required_keys__ == {"error_code", "message"}
assert PublicResult.__required_keys__ == {
    "schema_version", "ok", "status", "command", "record_refs", "hashes",
    "stage_outcomes", "warnings", "next_actions", "error", "exit_code", "data",
}
print("ok")
"""
    result = subprocess.run(
        [sys.executable, "-c", probe],
        cwd=tmp_path,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "ok"
