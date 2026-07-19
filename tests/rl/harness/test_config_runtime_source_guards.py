from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
RUNTIME_PATH = ROOT / "breadboard" / "rl" / "harness" / "config_runtime.py"
CONTRACTS_PATH = ROOT / "breadboard" / "rl" / "harness" / "contracts.py"

_FORBIDDEN_IMPORT_ROOTS = {
    "asyncio",
    "aiohttp",
    "cachetools",
    "functools",
    "http",
    "httpx",
    "os",
    "pathlib",
    "numpy",
    "random",
    "requests",
    "secrets",
    "socket",
    "ssl",
    "tempfile",
    "time",
    "urllib",
    "uuid",
}
_FORBIDDEN_HARNESS_IMPORTS = {"policy", "profiles", "sandbox"}
_FORBIDDEN_CALLS = {
    "builtins.open",
    "asyncio.open_connection",
    "asyncio.start_server",
    "datetime.datetime.now",
    "datetime.datetime.utcnow",
    "os.getenv",
    "http.client.HTTPConnection",
    "http.client.HTTPSConnection",
    "os.urandom",
    "pathlib.Path.cwd",
    "pathlib.Path.home",
    "random.choice",
    "random.randint",
    "random.random",
    "random.randrange",
    "secrets.token_bytes",
    "secrets.token_hex",
    "socket.create_connection",
    "socket.socket",
    "socket.getaddrinfo",
    "time.monotonic",
    "time.perf_counter",
    "time.time",
    "time.time_ns",
}
_FORBIDDEN_FIELD_NAMES = {
    "auth_header",
    "auth_headers",
    "base_url",
    "command",
    "config_path",
    "credential_path",
    "environment_name",
    "executable",
    "executable_object",
    "headers",
    "host_path",
    "raw_secret",
    "shell",
    "shell_command",
    "url",
}
_FORBIDDEN_PROFILE_LITERALS = {
    "breadboard_swe",
    "breadboard_terminal",
    "claude",
    "codex",
    "harnessprofile",
    "harnessprofileregistry",
    "oh-my-opencode",
    "opencode",
    "pi",
}


def _qualified_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _imports(tree: ast.AST) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def _v2_contract_field_names(tree: ast.Module) -> set[str]:
    fields: set[str] = set()
    for node in tree.body:
        # The frozen V1 public contracts end at line 115. WP3 appends its
        # closed value models below that boundary; include private supporting
        # records too so raw authority cannot hide in a nested contract.
        if not isinstance(node, ast.ClassDef) or node.lineno <= 115:
            continue
        for child in node.body:
            if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                fields.add(child.target.id)
    return fields


def test_config_runtime_has_no_ambient_authority_imports_or_calls() -> None:
    source = RUNTIME_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(RUNTIME_PATH))

    imports = _imports(tree)
    forbidden_imports = {
        module
        for module in imports
        if module.split(".", 1)[0] in _FORBIDDEN_IMPORT_ROOTS
        or (
            module.startswith("breadboard.rl.harness.")
            and module.rsplit(".", 1)[-1] in _FORBIDDEN_HARNESS_IMPORTS
        )
    }
    assert forbidden_imports == set()

    calls = {
        name
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (name := _qualified_name(node.func)) is not None
    }
    assert calls.isdisjoint(_FORBIDDEN_CALLS)
    forbidden_suffixes = {
        ".cwd",
        ".home",
        ".getaddrinfo",
        ".getenv",
        ".now",
        ".open",
        ".random",
        ".time",
        ".utcnow",
        ".urandom",
    }
    assert not {
        call
        for call in calls
        if call in {"hash", "open"}
        or any(call.endswith(suffix) for suffix in forbidden_suffixes)
    }

    imported_names = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    referenced_names = {
        name
        for node in ast.walk(tree)
        if (name := (
            node.id
            if isinstance(node, ast.Name)
            else node.attr
            if isinstance(node, ast.Attribute)
            else None
        )) is not None
    }
    assert "PolicyClient" not in imported_names
    assert "HarnessProfile" not in imported_names
    assert "HarnessProfileRegistry" not in imported_names
    assert "PolicyClient" not in referenced_names
    assert "HarnessProfile" not in referenced_names
    assert "HarnessProfileRegistry" not in referenced_names


def test_config_runtime_contains_no_profile_family_dispatch_literals() -> None:
    tree = ast.parse(RUNTIME_PATH.read_text(encoding="utf-8"), filename=str(RUNTIME_PATH))
    string_literals = {
        node.value.casefold()
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    identifiers = {
        node.id.casefold() for node in ast.walk(tree) if isinstance(node, ast.Name)
    }
    attributes = {
        node.attr.casefold() for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    observed = string_literals | identifiers | attributes

    assert observed.isdisjoint(_FORBIDDEN_PROFILE_LITERALS)
    assert all("config_family" not in value for value in observed)
    assert all("profile_name" not in value for value in observed)
    assert all("profile" not in value for value in identifiers | attributes)
    assert all("family" not in value for value in identifiers | attributes)


def test_v2_contracts_expose_no_raw_authority_fields() -> None:
    tree = ast.parse(CONTRACTS_PATH.read_text(encoding="utf-8"), filename=str(CONTRACTS_PATH))
    fields = _v2_contract_field_names(tree)

    assert fields
    assert fields.isdisjoint(_FORBIDDEN_FIELD_NAMES)


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    matches = [
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name
    ]
    assert len(matches) == 1
    return matches[0]


def test_wp4_weighted_oracle_is_fixed_width_sha256_without_rng_or_ambient_inputs() -> None:
    source = RUNTIME_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(RUNTIME_PATH))
    weighted_nodes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(child, ast.Constant)
            and child.value == b"bb-weighted-v1\x00"
            for child in ast.walk(node)
        )
    ]

    assert len(weighted_nodes) == 1
    weighted = weighted_nodes[0]
    assert isinstance(weighted, ast.FunctionDef)
    calls = {
        name
        for node in ast.walk(weighted)
        if isinstance(node, ast.Call)
        and (name := _qualified_name(node.func)) is not None
    }
    assert calls.isdisjoint(_FORBIDDEN_CALLS)
    assert not {
        name
        for name in calls
        if name in {"hash", "randint", "randrange", "shuffle", "uuid4"}
        or name.endswith((".choice", ".getrandbits", ".randbytes", ".random", ".shuffle"))
    }
    strings = {
        node.value.casefold()
        for node in ast.walk(weighted)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }
    assert strings.isdisjoint(_FORBIDDEN_PROFILE_LITERALS)
    assert b"bb-weighted-v1\x00" in {
        node.value
        for node in ast.walk(weighted)
        if isinstance(node, ast.Constant) and isinstance(node.value, bytes)
    }


def test_wp4_resolution_has_one_deep_seam_and_local_policy_registry_protocol() -> None:
    tree = ast.parse(RUNTIME_PATH.read_text(encoding="utf-8"), filename=str(RUNTIME_PATH))
    runtime = _class(tree, "ConfigRuntime")
    resolve_methods = [
        node
        for node in runtime.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "resolve_episode"
    ]
    assert len(resolve_methods) == 1
    assert isinstance(resolve_methods[0], ast.FunctionDef)

    registry = _class(tree, "PolicyCapabilityRegistry")
    observe_methods = [
        node
        for node in registry.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "observe"
    ]
    assert len(observe_methods) == 1
    assert isinstance(observe_methods[0], ast.FunctionDef)
    assert not any(isinstance(node, (ast.Await, ast.Yield, ast.YieldFrom)) for node in ast.walk(observe_methods[0]))
    observe = observe_methods[0]
    assert [argument.arg for argument in observe.args.args] == ["self"]
    assert [argument.arg for argument in observe.args.kwonlyargs] == [
        "binding",
        "subject",
        "now",
    ]
    assert observe.args.vararg is None
    assert observe.args.kwarg is None
    assert observe.args.defaults == []
    assert observe.args.kw_defaults == [None, None, None]
    assert _qualified_name(observe.returns) == "c.PolicyCapabilityObservation"

    observe_calls = [
        name
        for node in ast.walk(runtime)
        if isinstance(node, ast.Call)
        and (name := _qualified_name(node.func)) is not None
        and name.endswith(".observe")
    ]
    assert observe_calls == ["self._policy_capabilities.observe"]

    initializers = [
        node
        for node in runtime.body
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    ]
    assert len(initializers) == 1
    initializer = initializers[0]
    keyword_names = [argument.arg for argument in initializer.args.kwonlyargs]
    policy_index = keyword_names.index("policy_capabilities")
    policy_default = initializer.args.kw_defaults[policy_index]
    assert isinstance(policy_default, ast.Constant) and policy_default.value is None


def test_wp4_contracts_have_no_wrapper_claim_or_live_policy_authority_fields() -> None:
    tree = ast.parse(CONTRACTS_PATH.read_text(encoding="utf-8"), filename=str(CONTRACTS_PATH))
    wp4_contract_names = {
        "PolicyCapabilityObservation",
        "PolicyCapabilityVector",
        "PolicyCapabilityAttestationRecord",
        "ResolveEpisodeRequest",
        "SelectionRecord",
        "EffectiveExecutionPlan",
        "ResolvedEpisodePlan",
    }
    classes = {
        node.name: node for node in tree.body if isinstance(node, ast.ClassDef)
    }
    assert wp4_contract_names <= classes.keys()

    forbidden = _FORBIDDEN_FIELD_NAMES | {
        "api_key",
        "caller_capabilities",
        "credential",
        "credential_value",
        "dns_name",
        "episode_claim",
        "live_model",
        "model_version_claim",
        "provider_response",
        "routing_claim",
        "wrapper_claim",
        "wrapper_policy_capabilities",
    }
    for name in wp4_contract_names:
        fields = {
            child.target.id
            for child in classes[name].body
            if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
        }
        assert fields
        assert fields.isdisjoint(forbidden), name


def test_wp4_policy_attestation_source_binds_authorized_signers_and_verification_policy() -> None:
    contracts_tree = ast.parse(
        CONTRACTS_PATH.read_text(encoding="utf-8"), filename=str(CONTRACTS_PATH)
    )
    attestation = _class(contracts_tree, "PolicyCapabilityAttestationRecord")
    attestation_fields = {
        child.target.id
        for child in attestation.body
        if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name)
    }
    assert {
        "authorized_signer_key_ids",
        "signature_verification_policy_digest",
        "attestation_digest",
        "validity",
        "revocation",
    } <= attestation_fields

    runtime_tree = ast.parse(
        RUNTIME_PATH.read_text(encoding="utf-8"), filename=str(RUNTIME_PATH)
    )
    runtime = _class(runtime_tree, "ConfigRuntime")
    observe = next(
        node
        for node in runtime.body
        if isinstance(node, ast.FunctionDef) and node.name == "_observe_policy"
    )
    observed_attributes = {
        node.attr for node in ast.walk(observe) if isinstance(node, ast.Attribute)
    }
    assert "authorized_signer_key_ids" in observed_attributes
    assert "signer_key_id" in observed_attributes
    assert "validity" in observed_attributes
    assert "revocation" in observed_attributes


def test_v1_shadow_contracts_are_absent_from_runtime_and_contracts() -> None:
    forbidden_names = {
        "V1ProfileSnapshot",
        "V1ShadowCatalogEntry",
        "V1ShadowCatalogManifest",
        "V1_PROFILE_SNAPSHOT",
        "V1_SHADOW_CATALOG",
    }
    for path in (RUNTIME_PATH, CONTRACTS_PATH):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        referenced = {
            node.id if isinstance(node, ast.Name) else node.attr
            for node in ast.walk(tree)
            if isinstance(node, (ast.Name, ast.Attribute))
        }
        assert referenced.isdisjoint(forbidden_names)
