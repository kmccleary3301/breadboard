from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

from breadboard.product.harness.model import HarnessDefinition
from breadboard.product.harness.templates import (
    load_minimal_harness,
    minimal_template_path,
    minimal_template_text,
)
from breadboard.product.harness.validate import validate_harness_definition

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "agent_configs" / "templates" / "minimal_harness.v3.yaml"


def test_checked_in_template_is_exact_minimal_canonical_model() -> None:
    text = minimal_template_text()
    document = yaml.safe_load(text)
    assert minimal_template_path() == TEMPLATE
    assert text == TEMPLATE.read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 80
    assert document["schema_version"] == "bb.harness_definition.v1"
    assert document["version"] == 1
    assert set(document) == {
        "schema_version",
        "version",
        "workspace",
        "providers",
        "modes",
        "loop",
    }
    assert all(marker not in text for marker in ("implementations/", "import_path", "runtime"))
    assert validate_harness_definition(document) == ()
    harness = load_minimal_harness()
    assert isinstance(harness, HarnessDefinition)
    assert harness.as_dict() == document

def test_wheel_import_loads_template_from_distribution_data_root(tmp_path: Path) -> None:
    wheelhouse, outside_repo = tmp_path / "wheelhouse", tmp_path / "outside-repo"
    for directory in (wheelhouse, outside_repo):
        directory.mkdir()
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["PYTHONNOUSERSITE"] = "1"

    def run(*command: str) -> str:
        return subprocess.run(
            command,
            cwd=outside_repo,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        ).stdout

    run(sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
        "--wheel-dir", str(wheelhouse), str(ROOT))
    wheel = next(wheelhouse.glob("*.whl"))
    install_root = tmp_path / "install"
    run(sys.executable, "-m", "pip", "install", "--no-deps",
        "--target", str(install_root), str(wheel))
    script = (
        f"import sys; sys.path.insert(0, {str(install_root)!r}); import json; "
        "from breadboard.product.harness.templates import "
        "load_minimal_harness, minimal_template_path, minimal_template_text; "
        "print(json.dumps({'path': str(minimal_template_path()), "
        "'text': minimal_template_text(), 'document': load_minimal_harness().as_dict()}))"
    )
    payload = json.loads(run(sys.executable, "-I", "-c", script))
    expected_text = TEMPLATE.read_text(encoding="utf-8")
    expected_installed_path = (
        install_root / "agent_configs/templates/minimal_harness.v3.yaml")
    assert Path(payload["path"]) == expected_installed_path
    assert payload["text"] == expected_text
    assert payload["document"] == yaml.safe_load(expected_text)
