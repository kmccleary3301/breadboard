from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def _clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def test_editable_install_exposes_console_and_runtime_packages_outside_repo(
    tmp_path: Path,
) -> None:
    venv = tmp_path / "venv"
    outside_repo = tmp_path / "unrelated-working-directory"
    outside_repo.mkdir()

    subprocess.run(
        [sys.executable, "-m", "venv", "--system-site-packages", str(venv)],
        check=True,
        capture_output=True,
        text=True,
    )
    venv_python = venv / "bin" / "python"
    breadboard = venv / "bin" / "breadboard"
    environment = _clean_environment()
    subprocess.run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--no-build-isolation",
            "--editable",
            str(ROOT),
        ],
        cwd=outside_repo,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    help_result = subprocess.run(
        [str(breadboard), "--help"],
        cwd=outside_repo,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert help_result.returncode == 0, help_result.stderr
    assert help_result.stdout.startswith("usage: breadboard")
    assert "harness" in help_result.stdout
    assert "lane" in help_result.stdout

    describe_result = subprocess.run(
        [str(breadboard), "--json", "system", "describe"],
        cwd=outside_repo,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert describe_result.returncode == 0, describe_result.stderr
    describe = json.loads(describe_result.stdout)
    assert describe["schema_version"] == "bb.cli.result.v1"
    assert describe["command"] == ["system", "describe"]

    import_result = subprocess.run(
        [
            str(venv_python),
            "-I",
            "-c",
            (
                "import adaptive_iter, json, agentic_coder_prototype, breadboard, "
                "breadboard_sdk, conformance; "
                "print(json.dumps([adaptive_iter.__file__, "
                "agentic_coder_prototype.__file__, breadboard.__file__, "
                "breadboard_sdk.__file__, conformance.__file__]))"
            ),
        ],
        cwd=outside_repo,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )

    assert import_result.returncode == 0, import_result.stderr
    origins = [Path(value).resolve() for value in json.loads(import_result.stdout)]
    assert all(origin.is_relative_to(ROOT) for origin in origins), origins
