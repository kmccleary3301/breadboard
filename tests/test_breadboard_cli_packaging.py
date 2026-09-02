from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]


def _clean_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    for key in tuple(environment):
        if key.endswith(("_API_KEY", "_ACCESS_TOKEN", "_AUTH_TOKEN")) or key in {
            "BREADBOARD_CREDENTIAL_DB",
            "BREADBOARD_CREDENTIAL_STORE_PATH",
            "BREADBOARD_STATE_DIR",
        }:
            environment.pop(key)
    environment["PYTHONNOUSERSITE"] = "1"
    return environment


def _build_wheel(tmp_path: Path) -> Path:
    wheelhouse = tmp_path / "wheelhouse"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(wheelhouse), str(ROOT)],
        cwd=tmp_path,
        env=_clean_environment(),
        check=True,
        capture_output=True,
        text=True,
    )
    wheels = list(wheelhouse.glob("*.whl"))
    assert len(wheels) == 1
    return wheels[0]


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
    assert "lane" not in help_result.stdout
    internal_environment = dict(environment, BREADBOARD_ENABLE_E4_API="1")
    internal_help = subprocess.run(
        [str(breadboard), "--help"],
        cwd=outside_repo,
        env=internal_environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert internal_help.returncode == 0, internal_help.stderr
    assert "lane" in internal_help.stdout

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
    assert describe["data"]["system"] == "breadboard"
    assert describe["data"]["operation_count"] == 26
    assert describe["data"]["internal_extensions"] == []

    import_result = subprocess.run(
        [
            str(venv_python),
            "-I",
            "-c",
            (
                "import importlib.util, json; "
                "names=['adaptive_iter','breadboard_engine','breadboard','breadboard.product','breadboard.artifacts','breadboard_sdk']; "
                "specs=[importlib.util.find_spec(name) for name in names]; "
                "assert all(spec is not None for spec in specs); "
                "print(json.dumps([spec.origin for spec in specs]))"
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


def test_built_wheel_owns_runtime_resources_and_excludes_repository_debris(
    tmp_path: Path,
) -> None:
    wheel = _build_wheel(tmp_path)

    with ZipFile(wheel) as archive:
        names = set(archive.namelist())
        entry_points_path = next(
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        )
        entry_points = archive.read(entry_points_path).decode("utf-8")

    required = {
        "agent_configs/templates/daily_driver.v1.yaml",
        "agent_configs/templates/daily_driver_roles.v1.json",
        "agent_configs/templates/minimal_harness.v3.yaml",
        "agent_configs/templates/prompts/daily_driver_system.md",
        "agent_configs/templates/prompts/minimal_system.md",
        "breadboard_engine/__init__.py",
        "breadboard/product/cli/main.py",
        "breadboard/product/operations/generated_bindings.py",
        "breadboard_engine/compilation/bundle.py",
        "breadboard_engine/e4_targets.py",
        "breadboard_sdk/__init__.py",
        "breadboard_sdk/generated/__init__.py",
        "breadboard_sdk/generated/public_bindings.py",
        "breadboard_sdk/generated/public_surface_manifest.v1.json",
        "config/product/tui-release.json",
        "conformance/comparators/registry.json",
        "contracts/kernel/manifests/bb.engine_conformance_manifest.v1.schema.json",
        "contracts/kernel/packs.v1.json",
        "contracts/kernel/schemas/payloads/bb.payload.message.user.v1.schema.json",
        "contracts/public/operations.v2.json",
        "contracts/public/record_schemas.v1.json",
        "contracts/public/surface_inventory.v1.json",
        "implementations/prompts/todos/build.md",
        "implementations/prompts/todos/plan.md",
        "implementations/system_prompts/default.md",
        "implementations/tool_prompt_synthesis/pythonic/system_full.j2.md",
        "implementations/tools/defs/read_file.yaml",
    }
    assert required <= names, sorted(required - names)
    assert entry_points == (
        "[console_scripts]\n"
        "breadboard = breadboard.product.cli:main\n"
    )

    forbidden_prefixes = (
        "agentic_coder_prototype/",
        "breadboard/optimize/",
        "breadboard/rl/",
        "breadboard/search/",
        ".beads/",
        ".git/",
        "artifacts/",
        "breadboard_ext/",
        "build/",
        "docs_tmp/",
        "scripts/archive/",
        "sdk/",
        "tests/",
        "tui_skeleton/",
    )
    forbidden_suffixes = (
        ".db",
        ".pyc",
        ".sqlite",
        ".sqlite3",
        ".tgz",
        ".zip",
    )
    forbidden = sorted(
        name
        for name in names
        if name.startswith(forbidden_prefixes)
        or name.endswith(forbidden_suffixes)
        or "/__pycache__/" in name
        or "/.env" in name
    )
    assert forbidden == []


def test_built_wheel_clean_install_runs_public_surface_without_credentials(
    tmp_path: Path,
) -> None:
    wheel = _build_wheel(tmp_path)
    venv = tmp_path / "venv"
    outside_repo = tmp_path / "outside-repo"
    home = tmp_path / "home"
    outside_repo.mkdir()
    home.mkdir()
    environment = _clean_environment()
    environment["HOME"] = str(home)

    subprocess.run(
        ["uv", "venv", "--python", sys.executable, str(venv)],
        cwd=outside_repo,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    venv_python = venv / "bin" / "python"
    breadboard = venv / "bin" / "breadboard"
    subprocess.run(
        ["uv", "pip", "install", "--python", str(venv_python), str(wheel)],
        cwd=outside_repo,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )

    probe = f"""
import importlib.metadata
import json
import sys
from importlib.resources import files
from pathlib import Path
import breadboard
import breadboard_engine
import breadboard_sdk
from breadboard.product.operations.generated_bindings import (
    PUBLIC_OPERATION_BINDINGS as PRODUCT_OPERATION_BINDINGS,
)
from breadboard_sdk.generated import (
    PUBLIC_OPERATION_BINDINGS as SDK_OPERATION_BINDINGS,
)
from breadboard_sdk.generated import public_bindings as sdk_public_bindings
from breadboard.product.harness.default_profile import default_profile_identity, resolve_default_profile
from breadboard.product.cli.system import schemas
from breadboard.product.harness.templates import (
    daily_driver_model_roles_path,
    daily_driver_prompt_path,
    daily_driver_template_path,
    load_minimal_harness,
    minimal_template_path,
)
from breadboard.product.operation_catalog import product_operation_catalog
from breadboard_engine.compilation.primitive_records import get_spec
from breadboard_engine.e4_targets import (
    _resource_root,
    list_e4_target_ids,
    load_e4_target,
)
from breadboard_engine.e4_trace_parity import canonical_json_bytes

source_root = Path({str(ROOT)!r}).resolve()
distribution = importlib.metadata.distribution("breadboard-harness-cli")
site_root = Path(distribution.locate_file("")).resolve()
origins = [
    Path(module.__file__).resolve()
    for module in (breadboard, breadboard_engine, breadboard_sdk)
]
assert all(path.is_relative_to(site_root) for path in origins)
assert all(not path.is_relative_to(source_root) for path in origins)
target_resource_root = _resource_root().resolve()
assert target_resource_root == site_root / "config" / "e4_targets"
catalog = product_operation_catalog()
assert catalog["contract_id"] == "bb.public_operation_catalog.v2"
assert len(catalog["operations"]) == 26
template = load_minimal_harness().as_dict()
template_path = minimal_template_path().resolve()
assert template["schema_version"] == "bb.harness_definition.v1"
assert template_path.is_relative_to(site_root)
default_profile = default_profile_identity()
default_resolution = resolve_default_profile()
daily_paths = (
    daily_driver_template_path().resolve(),
    daily_driver_prompt_path().resolve(),
    daily_driver_model_roles_path().resolve(),
)
assert all(path.is_relative_to(site_root) for path in daily_paths)
assert default_profile["profile_id"] == "daily_driver.v1"
assert default_profile["effective_lock_hash"] == (
    default_resolution.compilation.lock["graph_hash"]
)
e4_imports = [
    name
    for name in sys.modules
    if name == "breadboard.product.evidence"
    or name.startswith("breadboard.product.evidence.")
    or name.startswith("scripts.e4_parity")
]
assert e4_imports == []
schema_result = schemas(["system", "schemas"], Path.cwd())
assert schema_result.ok and schema_result.data["schema_count"] > 0
kernel_schema = get_spec("bb.work_item.v2").schema_path.resolve()
assert kernel_schema.is_file() and kernel_schema.is_relative_to(site_root)
generated = json.loads(
    files("breadboard_sdk.generated")
    .joinpath("public_surface_manifest.v1.json")
    .read_text(encoding="utf-8")
)
assert generated["catalog_id"] == "bb.public_operation_catalog.v2"
assert len(generated["operations"]) == 26
assert len(SDK_OPERATION_BINDINGS) == len(PRODUCT_OPERATION_BINDINGS) == 26
assert sdk_public_bindings.PUBLIC_OPERATION_BINDINGS is SDK_OPERATION_BINDINGS
assert files("breadboard_sdk.generated").joinpath("public_bindings.py").is_file()
target_ids = list_e4_target_ids()
assert target_ids == ("oh-my-pi@16.2.13", "pi@0.57.1")
pi_target = load_e4_target("pi@0.57.1")
omp_target = load_e4_target("oh-my-pi@16.2.13")
assert pi_target.descriptor["upstream"]["package"]["integrity"].startswith("sha512-")
assert omp_target.descriptor["upstream"]["source"]["commit"] == (
    "5356713eae60e67ee64d9b02e3b5e377d248ee7f"
)
assert "target_id: pi@0.57.1" in pi_target.read_asset_text("harness.yaml")
assert canonical_json_bytes({{"b": 1, "a": 2}}) == b'{{"a":2,"b":1}}'
assert distribution.version == "0.0.0"
print(json.dumps({{
    "distribution": distribution.metadata["Name"],
    "version": distribution.version,
    "operation_count": len(catalog["operations"]),
    "schema_count": schema_result.data["schema_count"],
    "generated_operation_count": len(generated["operations"]),
    "profile_id": default_profile["profile_id"],
    "profile_hash": default_profile["effective_lock_hash"],
    "e4_import_count": len(e4_imports),
    "e4_target_ids": target_ids,
}}))
"""
    probe_result = subprocess.run(
        [str(venv_python), "-I", "-c", probe],
        cwd=outside_repo,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert json.loads(probe_result.stdout) == {
        "distribution": "breadboard-harness-cli",
        "version": "0.0.0",
        "operation_count": 26,
        "schema_count": 27,
        "generated_operation_count": 26,
        "profile_id": "daily_driver.v1",
        "profile_hash": (
            "sha256:6ea299b2d3ee382a8d8397cd5ed32080e99f8ae8b6a48006fce1ecad6859c10f"
        ),
        "e4_import_count": 0,
        "e4_target_ids": ["oh-my-pi@16.2.13", "pi@0.57.1"],
    }

    help_result = subprocess.run(
        [str(breadboard), "--help"],
        cwd=outside_repo,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
    )
    assert help_result.stdout.startswith("usage: breadboard")
    assert "harness" in help_result.stdout
    assert "lane" not in help_result.stdout
    minimal_config = next(
        venv.glob(
            "lib/python*/site-packages/agent_configs/templates/minimal_harness.v3.yaml"
        )
    )
    explain_result = subprocess.run(
        [str(breadboard), "harness", "explain", str(minimal_config)],
        cwd=outside_repo,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    assert explain_result.returncode == 0, explain_result.stderr
    assert "minimal_harness" in explain_result.stdout


    payloads = {}
    for command, expected_stage in (
        (["system", "describe"], "system.describe"),
        (["system", "health"], "system.health"),
    ):
        result = subprocess.run(
            [str(breadboard), "--json", *command],
            cwd=outside_repo,
            env=environment,
            check=True,
            capture_output=True,
            text=True,
        )
        payload = json.loads(result.stdout)
        assert payload["ok"] is True
        assert payload["stage_outcomes"][0]["stage"] == expected_stage
        payloads[expected_stage] = payload
    assert payloads["system.describe"]["data"]["operation_count"] == 26
    assert (
        payloads["system.describe"]["data"]["default_profile"]["profile_id"]
        == "daily_driver.v1"
    )
    assert (
        payloads["system.describe"]["hashes"]["profile"]
        == (
            payloads["system.describe"]["data"]["default_profile"][
                "effective_lock_hash"
            ]
        )
    )
