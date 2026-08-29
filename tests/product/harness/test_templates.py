from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest
import yaml
from breadboard_engine.model_roles import compile_model_roles

from breadboard.product.harness import templates as harness_templates
from breadboard.product.harness.model import HarnessDefinition
from breadboard.product.harness.templates import (
    daily_driver_model_roles_path,
    daily_driver_model_roles_text,
    daily_driver_prompt_path,
    daily_driver_prompt_text,
    daily_driver_template_path,
    daily_driver_template_text,
    load_daily_driver_harness,
    load_daily_driver_model_roles,
    load_minimal_harness,
    minimal_template_path,
    minimal_template_text,
)
from breadboard.product.harness.validate import validate_harness_definition

ROOT = Path(__file__).resolve().parents[3]
TEMPLATE = ROOT / "agent_configs" / "templates" / "minimal_harness.v3.yaml"
DAILY_TEMPLATE = ROOT / "agent_configs" / "templates" / "daily_driver.v1.yaml"
DAILY_PROMPT = (
    ROOT / "agent_configs" / "templates" / "prompts" / "daily_driver_system.md"
)
DAILY_MODEL_ROLES = ROOT / "agent_configs" / "templates" / "daily_driver_roles.v1.json"


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

def test_package_resource_lookup_rejects_symlinked_data_root(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    package = tmp_path / "package"
    external = tmp_path / "external-agent-configs"
    resource = external / "templates" / "daily_driver.v1.yaml"
    resource.parent.mkdir(parents=True)
    resource.write_text("external\n", encoding="utf-8")
    package.mkdir()
    (package / "agent_configs").symlink_to(
        external,
        target_is_directory=True,
    )
    monkeypatch.setattr(
        harness_templates,
        "__file__",
        str(
            package / "breadboard" / "product" / "harness"
            / "templates.py"
        ),
    )

    with pytest.raises(ValueError, match="cannot traverse symlinks"):
        harness_templates._data_path(
            Path("agent_configs/templates/daily_driver.v1.yaml")
        )


def test_checked_in_daily_driver_is_public_bounded_and_provider_free() -> None:
    text = daily_driver_template_text()
    prompt = daily_driver_prompt_text()
    roles_text = daily_driver_model_roles_text()
    document = yaml.safe_load(text)
    roles = load_daily_driver_model_roles()

    assert daily_driver_template_path() == DAILY_TEMPLATE
    assert daily_driver_prompt_path() == DAILY_PROMPT
    assert daily_driver_model_roles_path() == DAILY_MODEL_ROLES
    assert text == DAILY_TEMPLATE.read_text(encoding="utf-8")
    assert prompt == DAILY_PROMPT.read_text(encoding="utf-8")
    assert roles_text == DAILY_MODEL_ROLES.read_text(encoding="utf-8")
    assert roles == json.loads(roles_text)
    assert document["schema_version"] == "bb.harness_definition.v1"
    assert document["version"] == 1
    assert len(text.splitlines()) <= 80
    assert "dossier" not in document
    assert document["providers"]["default_model"] == "mock/reference"
    assert document["prompts"]["packs"]["base"]["system"] == (
        "prompts/daily_driver_system.md"
    )
    assert document["permissions"] == {
        "options": {"mode": "prompt"},
        "shell": {"default": "ask"},
    }
    assert document["modes"] == [{
        "name": "coding",
        "prompt": "@pack(base).system",
        "tools_enabled": [
            "read_file",
            "list_dir",
            "apply_unified_patch",
            "create_file_from_block",
            "run_shell",
            "TodoWrite",
            "mark_task_complete",
        ],
    }]
    assert set(roles["roles"]) == {"default", "designer", "plan", "slow", "smol", "task", "vision"}
    assert all(
        binding == {
            "primary": {
                "provider_id": "mock",
                "model_id": "reference",
                "account_selector": {"mode": "none", "pin": "session"},
            },
            "fallbacks": [],
            "fallback_on": [],
        }
        for binding in roles["roles"].values()
    )
    role_lock = compile_model_roles(roles)
    assert role_lock["defaults"]["role"] == "default"
    assert all("account_id" not in binding["primary"] for binding in role_lock["roles"].values())
    assert "implementations/" not in text
    assert validate_harness_definition(document) == ()
    harness = load_daily_driver_harness()
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

    run("uv", "build", "--wheel", "--out-dir", str(wheelhouse), str(ROOT))
    wheel = next(wheelhouse.glob("*.whl"))
    install_root = tmp_path / "install"
    with ZipFile(wheel) as archive:
        archive.extractall(install_root)
    script = (
        f"import sys; sys.path.insert(0, {str(install_root)!r}); import json; "
        "from breadboard.product.harness.templates import "
        "daily_driver_model_roles_path, daily_driver_model_roles_text, daily_driver_prompt_path, daily_driver_prompt_text, daily_driver_template_path, daily_driver_template_text, load_daily_driver_harness, load_daily_driver_model_roles, "
        "load_minimal_harness, minimal_template_path, minimal_template_text; from breadboard.product.coordination.work_items import WorkItem; "
        "from breadboard_engine.compilation.primitive_records import get_spec, _validator; candidate = _validator(get_spec('bb.work_item.v2').schema_path); "
        "print(json.dumps({'path': str(minimal_template_path()), 'text': minimal_template_text(), 'document': load_minimal_harness().as_dict(), 'daily_path': str(daily_driver_template_path()), 'daily_text': daily_driver_template_text(), 'daily_document': load_daily_driver_harness().as_dict(), 'prompt_path': str(daily_driver_prompt_path()), 'prompt_text': daily_driver_prompt_text(), 'model_roles_path': str(daily_driver_model_roles_path()), 'model_roles_text': daily_driver_model_roles_text(), 'model_roles_document': load_daily_driver_model_roles(), 'work_status': WorkItem.create('wheel').read_model.status, 'schema_id': candidate.schema['$id']}))"
    )
    payload = json.loads(run(sys.executable, "-I", "-c", script))
    expected_text = TEMPLATE.read_text(encoding="utf-8")
    expected_installed_path = (
        install_root / "agent_configs/templates/minimal_harness.v3.yaml")
    assert Path(payload["path"]) == expected_installed_path
    assert payload["text"] == expected_text
    assert payload["document"] == yaml.safe_load(expected_text) and payload["work_status"] == "ready" and payload["schema_id"] == "https://breadboard.dev/contracts/kernel/schemas/bb.work_item.v2.schema.json"
    expected_daily_path = (
        install_root / "agent_configs/templates/daily_driver.v1.yaml")
    expected_prompt_path = (
        install_root / "agent_configs/templates/prompts/daily_driver_system.md")
    assert Path(payload["daily_path"]) == expected_daily_path
    assert payload["daily_text"] == DAILY_TEMPLATE.read_text(encoding="utf-8")
    assert payload["daily_document"] == yaml.safe_load(payload["daily_text"])
    assert Path(payload["prompt_path"]) == expected_prompt_path
    assert payload["prompt_text"] == DAILY_PROMPT.read_text(encoding="utf-8")
    expected_model_roles_path = install_root / "agent_configs/templates/daily_driver_roles.v1.json"
    assert Path(payload["model_roles_path"]) == expected_model_roles_path
    assert payload["model_roles_text"] == DAILY_MODEL_ROLES.read_text(encoding="utf-8")
    assert payload["model_roles_document"] == json.loads(payload["model_roles_text"])
