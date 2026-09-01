from __future__ import annotations

import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
BASE_PATH = ROOT / "breadboard" / "rl" / "harness" / "runners" / "base.py"
TERMINAL_PATH = ROOT / "breadboard" / "rl" / "harness" / "runners" / "terminal.py"
CONDUCTOR_PATH = ROOT / "breadboard" / "rl" / "harness" / "runners" / "conductor.py"
RUNNER_PATHS = (BASE_PATH, TERMINAL_PATH, CONDUCTOR_PATH)
_ALLOWED_IMPORTS_BY_PATH = {
    BASE_PATH: frozenset(
        {
            "__future__",
            "collections.abc",
            "dataclasses",
            "enum",
            "math",
            "types",
            "typing",
            "breadboard.rl.harness.contracts",
        }
    ),
    TERMINAL_PATH: frozenset(
        {
            "__future__",
            "asyncio",
            "collections.abc",
            "dataclasses",
            "contextvars",
            "json",
            "typing",
            "breadboard.rl.harness.runners.base",
            "breadboard.rl.harness.runner_identity",
        }
    ),
    CONDUCTOR_PATH: frozenset(
        {
            "__future__",
            "asyncio",
            "collections.abc",
            "contextvars",
            "dataclasses",
            "decimal",
            "json",
            "math",
            "re",
            "typing",
            "breadboard_engine.compilation.contracts",
            "breadboard.rl.harness.contracts",
            "breadboard.rl.harness.runners.base",
            "breadboard.rl.harness.runner_identity",
        }
    ),
}
_DENIED_IMPORT_PREFIXES = (
    "dotenv",
    "importlib",
    "os",
    "pathlib",
    "socket",
    "subprocess",
    "breadboard_engine.agent",
    "breadboard_engine.agent_llm_openai",
    "breadboard_engine.conductor",
    "breadboard_engine.provider",
    "breadboard.rl.harness.api",
    "breadboard.rl.harness.config_runtime",
    "breadboard.rl.harness.evidence",
    "breadboard.rl.harness.materialization",
    "breadboard.rl.harness.policy",
    "breadboard.rl.harness.profiles",
    "breadboard.rl.harness.sandbox",
    "breadboard.rl.harness.sandbox_driver",
    "breadboard.rl.harness.sandbox_docker",
    "breadboard.rl.harness.service",
    "breadboard.sandbox_driver",
    "breadboard.sandbox_docker",
    "responses_api_agents",
    "recipe",
    "scripts",
    "launch",
)
_FORBIDDEN_CALLS = {
    "__import__",
    "builtins.__import__",
    "builtins.open",
    "eval",
    "exec",
    "getattr",
    "globals",
    "importlib.import_module",
    "locals",
    "open",
    "os.chdir",
    "os.fchdir",
    "os.get_exec_path",
    "os.getcwd",
    "os.getcwdb",
    "os.getenv",
    "os.putenv",
    "os.unsetenv",
    "pathlib.Path.cwd",
    "pathlib.Path.home",
    "pathlib.Path.open",
    "socket.getaddrinfo",
    "socket.socket",
    "subprocess.Popen",
    "subprocess.call",
    "subprocess.run",
}
_FORBIDDEN_ATTRIBUTES = {
    "chdir",
    "environ",
    "get_exec_path",
    "getcwd",
    "getcwdb",
    "getenv",
    "putenv",
    "unsetenv",
}
_FORBIDDEN_FAMILY_IDENTIFIERS = {
    "claude",
    "codex",
    "conductor",
    "harnessprofile",
    "harnessprofileregistry",
    "oh-my-opencode",
    "opencode",
    "pi",
    "swe",
}


def _family_tokens(value: str) -> frozenset[str]:
    return frozenset(token for token in re.split(r"[^a-z0-9]+", value.casefold()) if token)


def _qualified_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _trees() -> list[tuple[Path, ast.Module]]:
    return [
        (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
        for path in RUNNER_PATHS
    ]


def _imports(tree: ast.AST) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _has_prefix(value: str, prefix: str) -> bool:
    return value == prefix or value.startswith(prefix + ".")


def _disallowed_imports(tree: ast.AST, allowed: frozenset[str]) -> set[str]:
    return {
        imported
        for imported in _imports(tree)
        if imported not in allowed
        or any(_has_prefix(imported, prefix) for prefix in _DENIED_IMPORT_PREFIXES)
    }


def _ambient_violations(tree: ast.AST) -> tuple[set[str], set[str], set[str]]:
    calls = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (name := _qualified_name(node.func)) is not None
        and name in _FORBIDDEN_CALLS
    }
    attributes = {
        node.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_ATTRIBUTES
    }
    global_writes = {
        name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Global, ast.Nonlocal))
        for name in node.names
    }
    return calls, attributes, global_writes


def test_runner_modules_use_only_their_explicit_dependency_allowlists() -> None:
    for path, tree in _trees():
        assert _disallowed_imports(tree, _ALLOWED_IMPORTS_BY_PATH[path]) == set(), path


def test_runner_modules_have_no_ambient_reads_mutations_or_dynamic_imports() -> None:
    for path, tree in _trees():
        calls, attributes, global_writes = _ambient_violations(tree)
        assert calls == set(), path
        assert attributes == set(), path
        assert global_writes == set(), path


def test_runner_authority_guard_detects_representative_forbidden_code() -> None:
    ambient_tree = ast.parse(
        """
import os
os.getenv("TOKEN")
os.environ.get("TOKEN")
os.putenv("MODE", "unsafe")
os.unsetenv("MODE")
os.chdir("/tmp")
os.getcwd()
os.get_exec_path()
__import__("recipe.nemo_async")
builtins.__import__("responses_api_agents")
"""
    )
    assert _disallowed_imports(ambient_tree, frozenset()) == {"os"}
    calls, attributes, _ = _ambient_violations(ambient_tree)
    assert calls == {
        "__import__",
        "builtins.__import__",
        "os.chdir",
        "os.get_exec_path",
        "os.getcwd",
        "os.getenv",
        "os.putenv",
        "os.unsetenv",
    }
    assert attributes >= {
        "chdir",
        "environ",
        "get_exec_path",
        "getcwd",
        "getenv",
        "putenv",
        "unsetenv",
    }

    later_owner_tree = ast.parse(
        """
from breadboard.rl.harness import evidence, materialization, sandbox
from breadboard.rl.harness import sandbox_driver, sandbox_docker
import responses_api_agents.breadboard_agent.app
import recipe.nemo_async.agent_loop
import scripts.rl_phase5.bootstrap_phase5
"""
    )
    assert _disallowed_imports(later_owner_tree, frozenset()) == {
        "breadboard.rl.harness",
        "responses_api_agents.breadboard_agent.app",
        "recipe.nemo_async.agent_loop",
        "scripts.rl_phase5.bootstrap_phase5",
    }

    legacy_runtime_tree = ast.parse(
        """
from breadboard.rl.harness.policy import PolicyClient
from breadboard_engine.agent_llm_openai import OpenAIConductor
from breadboard_engine.provider.routing import provider_router
from breadboard_engine.provider.runtime import provider_registry
"""
    )
    assert _disallowed_imports(legacy_runtime_tree, frozenset()) == {
        "breadboard_engine.agent_llm_openai",
        "breadboard_engine.provider.routing",
        "breadboard_engine.provider.runtime",
        "breadboard.rl.harness.policy",
    }

    mutation_tree = ast.parse(
        """
state = None
def mutate():
    global state
    state = "shared"
"""
    )
    _, _, global_writes = _ambient_violations(mutation_tree)
    assert global_writes == {"state"}


def test_runner_registry_contains_no_profile_or_config_family_dispatch_literals() -> None:
    observed: set[str] = set()
    for _, tree in _trees():
        observed.update(
            node.value.casefold()
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        )
        observed.update(
            node.id.casefold()
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        )
        observed.update(
            node.attr.casefold()
            for node in ast.walk(tree)
            if isinstance(node, ast.Attribute)
        )

    assert observed.isdisjoint(_FORBIDDEN_FAMILY_IDENTIFIERS)
    forbidden_selector_tokens = _FORBIDDEN_FAMILY_IDENTIFIERS - {"conductor"}
    assert all(
        _family_tokens(value).isdisjoint(forbidden_selector_tokens)
        for value in observed
    )
    assert all("config_family" not in value for value in observed)
    assert all("profile_name" not in value for value in observed)


def test_runner_family_guard_detects_embedded_selector_names() -> None:
    assert "codex" in _family_tokens("codex_runtime")
    assert "codex" in _family_tokens("codex-v1")
    assert "claude" in _family_tokens("claude.profile")
    assert "opencode" in _family_tokens("oh-my-opencode")


def _named_function(tree: ast.Module, name: str) -> ast.FunctionDef:
    matches = [
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def _observation_linear_path_violations(
    function: ast.FunctionDef,
) -> tuple[int, int]:
    encode_calls = sum(
        1
        for node in ast.walk(function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "encode"
    )
    dumps_inside_loops = sum(
        1
        for loop in ast.walk(function)
        if isinstance(loop, (ast.For, ast.While))
        for node in ast.walk(loop)
        if isinstance(node, ast.Call)
        and _qualified_name(node.func) == "json.dumps"
    )
    return encode_calls, dumps_inside_loops


def test_json_observation_truncation_has_one_linear_prefix_pass() -> None:
    terminal_tree = next(tree for path, tree in _trees() if path == TERMINAL_PATH)
    function = _named_function(terminal_tree, "_json_observation")
    assert _observation_linear_path_violations(function) == (0, 0)


def test_json_observation_linear_guard_detects_reencoding_loop() -> None:
    bad_tree = ast.parse(
        """
def _json_observation(value, limit):
    preview = value
    while len(json.dumps({"preview": preview}).encode("utf-8")) > limit:
        preview = preview[:-1]
    return preview
"""
    )
    assert _observation_linear_path_violations(
        _named_function(bad_tree, "_json_observation")
    ) == (1, 1)
