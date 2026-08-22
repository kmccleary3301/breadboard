from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[3]
HARNESS = ROOT / "breadboard" / "rl" / "harness"


def test_legacy_modules_symbols_and_mutating_routes_are_deleted() -> None:
    assert not (HARNESS / "profiles.py").exists()
    assert not (HARNESS / "policy.py").exists()
    forbidden = {
        "HarnessProfile", "HarnessProfileRegistry", "BreadBoardEpisodeService",
        "SandboxLeaseManager", "V1ProfileSnapshot", "V1ShadowCatalogEntry",
        "V1ShadowCatalogManifest", "BREADBOARD_HARNESS_PROFILES_FILE",
        "BREADBOARD_HARNESS_PROFILES_JSON", "breadboard_swe", "breadboard_terminal",
    }
    production = "\n".join(path.read_text(encoding="utf-8") for path in HARNESS.glob("*.py"))
    assert forbidden.isdisjoint(production.split())
    api = ast.parse((HARNESS / "api.py").read_text(encoding="utf-8"))
    mutating_v1 = []
    for node in ast.walk(api):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if isinstance(decorator, ast.Call) and isinstance(decorator.func, ast.Attribute):
                path = decorator.args[0].value if decorator.args and isinstance(decorator.args[0], ast.Constant) else ""
                if path.startswith("/v1/") and decorator.func.attr in {"post", "put", "patch", "delete"}:
                    mutating_v1.append((decorator.func.attr, path))
    assert mutating_v1 == []


def test_v2_service_has_no_name_based_dispatch() -> None:
    source = (HARNESS / "service.py").read_text(encoding="utf-8")
    for literal in ("breadboard_swe", "breadboard_terminal", "breadboard-swe", "breadboard-terminal", "generated-zeta-unknown"):
        assert literal not in source
