from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
E4_OWNER = ROOT / "breadboard/product/evidence/e4"
PRODUCT_RUNTIME_FILES = (
    ROOT / "breadboard/product/cli/e4.py",
    ROOT / "breadboard_engine/api/e4/__init__.py",
    ROOT / "breadboard_engine/api/e4/models.py",
    ROOT / "breadboard_engine/api/e4/router.py",
)
FORBIDDEN_PREFIXES = ("scripts.e4_parity", "scripts.authoring")


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module is not None:
            imported.add(node.module)
        elif isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
    return imported


def test_product_e4_runtime_imports_only_internal_owner() -> None:
    paths = (*PRODUCT_RUNTIME_FILES, *E4_OWNER.rglob("*.py"))
    violations = {
        path.relative_to(ROOT).as_posix(): sorted(
            module
            for module in _top_level_imports(path)
            if module.startswith(FORBIDDEN_PREFIXES)
        )
        for path in paths
    }
    assert not {path: modules for path, modules in violations.items() if modules}


def test_default_app_loads_without_script_e4_modules() -> None:
    environment = dict(os.environ)
    environment["BREADBOARD_ENABLE_E4_API"] = "1"
    environment["BB_WORKSPACE_ROOT"] = str(ROOT)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import json,sys; "
                "import breadboard.product.cli.e4; "
                "from breadboard_engine.api.cli_bridge.app import create_app; "
                "create_app(); "
                "print(json.dumps(sorted(name for name in sys.modules "
                "if name.startswith(('scripts.e4_parity','scripts.authoring')))))"
            ),
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
        timeout=60,
    )
    assert json.loads(completed.stdout) == []


def test_compatibility_modules_delegate_to_internal_owners() -> None:
    from breadboard.product.evidence.e4 import (
        catalog_refs,
        compile_lane_lock,
        lane_definitions,
        run_lane,
    )
    from breadboard.product.evidence.e4.lane_manifest import load_lane_manifest
    from scripts.authoring.validate_lane import load_lane_manifest as legacy_loader
    from scripts.e4_parity import (
        catalog_refs as legacy_catalog_refs,
        compile_lane_lock as legacy_compile_lane_lock,
        lane_definitions as legacy_lane_definitions,
        run_lane as legacy_run_lane,
    )

    assert legacy_catalog_refs is catalog_refs
    assert (
        legacy_compile_lane_lock.compile_manifest is compile_lane_lock.compile_manifest
    )
    assert legacy_lane_definitions is lane_definitions
    assert legacy_run_lane.run_lane is run_lane.run_lane
    assert (
        legacy_compile_lane_lock.main.__module__
        == "scripts.e4_parity.compile_lane_lock"
    )
    assert legacy_run_lane.main.__module__ == "scripts.e4_parity.run_lane"
    assert not hasattr(compile_lane_lock, "main")
    assert not hasattr(run_lane, "main")
    assert legacy_loader is load_lane_manifest


@pytest.mark.parametrize(
    "script",
    (
        "compile_lane_lock.py",
        "generate_ct_rows.py",
        "generate_support_claims.py",
        "promote_lane_payload_source.py",
        "run_lane.py",
        "adapters/pi_p5_l1_capture.py",
        "adapters/pi_p5_l2_capture.py",
    ),
)
def test_compatibility_script_entrypoint_remains_executable(script: str) -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/e4_parity" / script), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout


def test_session_replay_script_entrypoint_remains_executable() -> None:
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts/replay_session_from_records.py"), "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
        timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    assert "usage:" in completed.stdout


def test_lane_definition_compatibility_adapter_is_product_owner() -> None:
    from breadboard.product.evidence.e4.adapters import lane_definition_build
    from scripts.e4_parity.adapters import lane_definition_build as legacy

    assert legacy is lane_definition_build


def test_run_lane_adapter_owns_argument_and_json_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from breadboard.product.evidence.e4 import run_lane as owner
    from scripts.e4_parity import run_lane as adapter

    report = {"ok": True, "lane_id": "fixture_lane", "stages": []}
    received: list[tuple[str, dict[str, object]]] = []

    def fake_run_lane(lane_id: str, **options: object) -> dict[str, object]:
        received.append((lane_id, options))
        return report

    monkeypatch.setattr(owner, "run_lane", fake_run_lane)
    exit_code = adapter.main(
        [
            "--lane",
            "fixture_lane",
            "--stage",
            "capture",
            "--out",
            str(tmp_path / "capture"),
            "--lane-def-dir",
            str(tmp_path / "lanes"),
            "--inventory",
            str(tmp_path / "inventory.json"),
            "--comparator-registry",
            str(tmp_path / "comparators.json"),
            "--json",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out) == report
    assert received == [
        (
            "fixture_lane",
            {
                "stage": "capture",
                "out_dir": tmp_path / "capture",
                "lane_def_dir": tmp_path / "lanes",
                "inventory_path": tmp_path / "inventory.json",
                "comparator_registry_path": tmp_path / "comparators.json",
                "promote_accepted": False,
                "defer_promotion_refresh": False,
                "defer_derived_writes": False,
            },
        )
    ]


def test_product_lane_capture_returns_structured_owner_result(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from breadboard.product.cli import e4
    from breadboard.product.cli import main as product_main
    from breadboard.product.evidence.lanes import MANIFEST_SCHEMA_VERSION

    manifest = tmp_path / "candidate_lane.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": MANIFEST_SCHEMA_VERSION,
                "lane_id": "candidate_lane",
                "status": "candidate",
                "execute": ["capture"],
                "reuse": ["compare"],
                "references": {
                    name: f"refs/{name}.json"
                    for name in (
                        "harness",
                        "target",
                        "adapter",
                        "source",
                        "comparator",
                        "policy",
                    )
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report = {"ok": True, "lane_id": "candidate_lane", "stages": []}
    monkeypatch.setenv("BREADBOARD_ENABLE_E4_API", "1")
    monkeypatch.setattr(e4.e4_runner, "run_lane", lambda *args, **kwargs: report)

    exit_code = product_main(["--json", "lane", "capture", str(manifest)])

    assert exit_code == 0
    result = json.loads(capsys.readouterr().out)
    assert result["data"]["capture"] == report


def test_active_e4_adapter_registry_impls_use_product_owner() -> None:
    registry = json.loads(
        (ROOT / "contracts/kernel/registries/e4_adapters.v1.json").read_text(encoding="utf-8")
    )
    active = [entry for entry in registry["entries"] if entry.get("status") == "active"]
    assert active
    assert all(
        str(entry["metadata"]["impl"]).startswith("breadboard.product.evidence.e4.adapters.")
        for entry in active
    )


def test_product_registry_adapters_import_without_script_modules(tmp_path: Path) -> None:
    environment = dict(os.environ)
    environment["BB_WORKSPACE_ROOT"] = str(tmp_path)
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import importlib,json,sys\n"
                "from pathlib import Path\n"
                "import breadboard.product.cli.e4\n"
                "registry=json.loads(Path('contracts/kernel/registries/e4_adapters.v1.json').read_text())\n"
                "for entry in registry['entries']:\n"
                "    if entry.get('status') != 'active':\n"
                "        continue\n"
                "    module_name,callable_name=entry['metadata']['impl'].split(':',1)\n"
                "    module=importlib.import_module(module_name)\n"
                "    getattr(module,callable_name)\n"
                "print(json.dumps(sorted(name for name in sys.modules if name.startswith('scripts.e4_parity'))))"
            ),
        ],
        cwd=ROOT,
        env=environment,
        text=True,
        capture_output=True,
        check=True,
        timeout=60,
    )
    assert json.loads(completed.stdout) == []


def test_product_live_e4_modules_have_no_legacy_imports() -> None:
    violations: dict[str, list[str]] = {}
    for path in sorted(E4_OWNER.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        refresh = next(
            (
                node
                for node in tree.body
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.name == "_refresh_promoted_bindings"
            ),
            None,
        )
        stale: list[str] = []
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.ImportFrom) and node.module is not None:
                modules.append(node.module)
            elif isinstance(node, ast.Import):
                modules.extend(alias.name for alias in node.names)
            for module in modules:
                approved_refresh = (
                    refresh is not None
                    and refresh.lineno <= node.lineno <= refresh.end_lineno
                    and module.startswith("scripts.e4_parity")
                )
                if not approved_refresh and module.startswith(
                    ("scripts.e4_parity", "scripts.replay_session_from_records")
                ):
                    stale.append(module)
        if stale:
            violations[path.relative_to(ROOT).as_posix()] = sorted(stale)
    assert not violations


def test_lane_acceptance_workspace_resolution_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from breadboard.product.evidence.e4 import lane_acceptance_artifacts
    from breadboard.product.evidence.e4.path_refs import ReferenceResolutionError

    monkeypatch.delenv("BB_WORKSPACE_ROOT", raising=False)
    with pytest.raises(ReferenceResolutionError):
        lane_acceptance_artifacts.resolve("docs_tmp/x")


def test_lane_ledger_paths_use_explicit_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from breadboard.product.evidence.e4 import (
        lane_acceptance_artifacts,
        oh_my_pi_p6_lane_projection,
    )

    monkeypatch.setenv("BB_WORKSPACE_ROOT", str(tmp_path))
    ledger_path = (
        tmp_path / "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
    )
    lane_spec = {"target": "oh_my_pi", "semantic_key": "fixture"}
    lane_feature_id = lane_acceptance_artifacts.feature_id(lane_spec)
    ledger_path.parent.mkdir(parents=True)
    ledger_path.write_text(
        json.dumps(
            {
                "rows": [
                    {"feature_id": lane_feature_id},
                    {
                        "e4_row_ref": "p6-fixture",
                        "family": "omp",
                        "feature_id": "p6-feature",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    assert lane_acceptance_artifacts.ledger_row_ref(lane_spec).startswith(
        f"docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json#{lane_feature_id}#"
    )
    assert oh_my_pi_p6_lane_projection._ledger_ref(
        {"config_id": "p6-fixture"},
        "omp",
    ).startswith(
        "docs_tmp/phase_15/BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json#p6-feature#"
    )
