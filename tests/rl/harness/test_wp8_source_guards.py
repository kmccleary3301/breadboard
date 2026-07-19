from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Iterable, Iterator


ROOT = Path(__file__).resolve().parents[3]
MODULES = {
    name: ROOT / f"breadboard/rl/harness/{name}.py"
    for name in ("api", "service", "evidence")
}

FunctionOwner = ast.FunctionDef | ast.AsyncFunctionDef

V1_MODEL_NAMES = {
    "AtomicEpisodeRunRequest",
    "EpisodeCreateRequest",
    "EpisodeCreateResponse",
    "EpisodeRunRequest",
    "EpisodeRunResponse",
    "EpisodeStateResponse",
    "SCHEMA_VERSION",
}
LEGACY_SERVICE_NAMES = {
    "profiles",
    "HarnessProfile",
    "HarnessProfileRegistry",
    "PolicyClient",
    "PolicyFactory",
    "PolicyGenerator",
    "SandboxLease",
    "SandboxLeaseManager",
    "_EpisodeTombstone",
    "_run_loop",
    "_execute_action",
    "_run_verifier",
    "_collect_artifacts",
    "_tombstone_for",
    "_persist_tombstone",
    "_load_tombstone_result",
    "_contains_policy_visible_image",
    "_json_observation",
}
AMBIENT_NAMES = {
    "os.environ",
    "os.getenv",
    "os.putenv",
    "os.unsetenv",
    "Path.home",
    "tempfile",
    "tempfile.gettempdir",
    "tempfile.mkdtemp",
    "tempfile.NamedTemporaryFile",
    "tempfile.TemporaryDirectory",
}
_FORBIDDEN_IDENTIFIER_TOKENS = {"profile", "profiles", "family", "provider", "provider_url", "base_url"}
_FORBIDDEN_LITERAL_WORD = re.compile(r"(?:^|[^a-z0-9_])(profile|profiles|family|provider|provider_url|base_url)(?:$|[^a-z0-9_])")


def _tree(module: str) -> ast.Module:
    path = MODULES[module]
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _qualified_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def _definitions(tree: ast.Module) -> Iterator[ast.ClassDef | FunctionOwner]:
    for node in tree.body:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node


def _definition(tree: ast.Module, name: str) -> ast.ClassDef | FunctionOwner:
    return next(node for node in _definitions(tree) if node.name == name)


def _method(tree: ast.Module, class_name: str, name: str) -> FunctionOwner:
    owner = _definition(tree, class_name)
    assert isinstance(owner, ast.ClassDef)
    return next(
        node
        for node in owner.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name
    )


def _calls(owner: ast.AST, name: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(owner)
        if isinstance(node, ast.Call) and _qualified_name(node.func) == name
    ]


def _attribute_calls(owner: ast.AST, attribute: str) -> list[ast.Call]:
    return [
        node
        for node in ast.walk(owner)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == attribute
    ]


def _referenced_names(owner: ast.AST) -> set[str]:
    values: set[str] = set()
    for node in ast.walk(owner):
        if isinstance(node, ast.Name):
            values.add(node.id)
        elif isinstance(node, ast.Attribute):
            qualified = _qualified_name(node)
            if qualified is not None:
                values.add(qualified)
                values.add(node.attr)
    return values


def _string_literals(owner: ast.AST) -> Iterator[tuple[int, str]]:
    for node in ast.walk(owner):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value


def _route(owner: FunctionOwner) -> str | None:
    for decorator in owner.decorator_list:
        if not isinstance(decorator, ast.Call) or not decorator.args:
            continue
        function = decorator.func
        if (
            isinstance(function, ast.Attribute)
            and function.attr in {"get", "post", "put", "patch", "delete"}
            and isinstance(decorator.args[0], ast.Constant)
            and isinstance(decorator.args[0].value, str)
        ):
            return decorator.args[0].value
    return None


def _api_v2_owners(tree: ast.Module) -> tuple[ast.ClassDef | FunctionOwner, ...]:
    owners: list[ast.ClassDef | FunctionOwner] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        route = _route(node) if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) else None
        if "V2" in node.name or "v2" in node.name.lower() or (route or "").startswith("/v2"):
            owners.append(node)
    return tuple(owners)


def _service_v2_owners(tree: ast.Module) -> tuple[ast.ClassDef | FunctionOwner, ...]:
    boundary = _definition(tree, "EpisodeLifecycleState").lineno
    return tuple(node for node in _definitions(tree) if node.lineno >= boundary)


def _owner_violations(
    owners: Iterable[ast.ClassDef | FunctionOwner],
) -> list[tuple[str, int, str]]:
    forbidden_names = V1_MODEL_NAMES | LEGACY_SERVICE_NAMES | AMBIENT_NAMES
    violations: list[tuple[str, int, str]] = []
    for owner in owners:
        for node in ast.walk(owner):
            if isinstance(node, (ast.Name, ast.Attribute)):
                name = _qualified_name(node)
                if name is None:
                    continue
                leaf = name.rsplit(".", 1)[-1]
                root = name.split(".", 1)[0]
                if (
                    name in forbidden_names
                    or leaf in forbidden_names
                    or root in AMBIENT_NAMES
                    or root == "policy"
                ):
                    violations.append((owner.name, node.lineno, name))
                if leaf.lower() in _FORBIDDEN_IDENTIFIER_TOKENS:
                    violations.append((owner.name, node.lineno, name))
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                literal = node.value.lower()
                if "/v1" in literal or "http://" in literal or "https://" in literal:
                    violations.append((owner.name, node.lineno, node.value))
                elif _FORBIDDEN_LITERAL_WORD.search(literal):
                    violations.append((owner.name, node.lineno, node.value))
    return violations


def _imports(tree: ast.Module) -> set[str]:
    imported: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    return imported


def _closure_authority_violations(
    service_owner: ast.AST, export_owner: FunctionOwner
) -> set[str]:
    violations: set[str] = set()
    if _calls(export_owner, "ExportAuthorizationV2"):
        violations.add("api_export_authorization_construction")

    if _calls(service_owner, "EvidenceObjectInputV2"):
        violations.add("workspace_file_artifact_fabrication")

    materialize_calls = _attribute_calls(service_owner, "materialize")
    validate_calls = _attribute_calls(service_owner, "validate_plan")
    if materialize_calls and (
        not validate_calls
        or any(
            _qualified_name(call.func.value)
            != "self._dependencies.evidence_authority"
            for call in (*materialize_calls, *validate_calls)
        )
    ):
        violations.add("unvalidated_evidence_authority")

    methods = (
        tuple(
            node
            for node in service_owner.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        )
        if isinstance(service_owner, ast.ClassDef)
        else (service_owner,)
    )
    for method in methods:
        closed_event_names = {
            target.id
            for assignment in ast.walk(method)
            if isinstance(assignment, ast.Assign)
            and isinstance(assignment.value, ast.Call)
            and _qualified_name(assignment.value.func) == "LifecycleEventV2"
            and any(
                keyword.arg == "to_state"
                and (
                    _qualified_name(keyword.value)
                    == "EpisodeLifecycleState.CLOSED.value"
                    or (
                        isinstance(keyword.value, ast.Constant)
                        and keyword.value.value == "closed"
                    )
                )
                for keyword in assignment.value.keywords
            )
            for target in assignment.targets
            if isinstance(target, ast.Name)
        }
        for call in ast.walk(method):
            if (
                not isinstance(call, ast.Call)
                or not isinstance(call.func, ast.Attribute)
                or call.func.attr not in {"append_transition", "_transition"}
            ):
                continue
            arguments = (*call.args, *(keyword.value for keyword in call.keywords))
            names = {
                node.id
                for argument in arguments
                for node in ast.walk(argument)
                if isinstance(node, ast.Name)
            }
            has_direct_closed = any(
                _qualified_name(node) == "EpisodeLifecycleState.CLOSED"
                or (
                    isinstance(node, ast.Constant)
                    and node.value == "closed"
                )
                for argument in arguments
                for node in ast.walk(argument)
            )
            if names & closed_event_names or has_direct_closed:
                violations.add("external_closed_transition_append")

    for header in _calls(export_owner, "Header"):
        alias = next(
            (
                keyword.value.value
                for keyword in header.keywords
                if keyword.arg == "alias"
                and isinstance(keyword.value, ast.Constant)
                and isinstance(keyword.value.value, str)
            ),
            "",
        )
        if alias in {
            "X-BreadBoard-Export-Not-Before",
            "X-BreadBoard-Export-Not-After",
        }:
            violations.add("caller_controlled_export_window")
        if header.args or any(keyword.arg == "default" for keyword in header.keywords):
            violations.add("ambient_export_default")
    return violations


def _export_route_owner(tree: ast.Module) -> FunctionOwner:
    owner = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _route(node) == "/v2/episodes/{episode_id}/exports/{role}"
    )
    return owner


def test_v2_owner_scanner_rejects_each_forbidden_authority_variant() -> None:
    mutant = ast.parse(
        """
def v2_mutant(EpisodeRunRequest):
    profile = profiles.lookup("legacy-family")
    client = policy.PolicyClient("https://provider.invalid/v1")
    lease = SandboxLeaseManager()
    home = Path.home()
    root = tempfile.mkdtemp()
    token = os.environ["TOKEN"]
    return EpisodeRunRequest, profile, client, lease, home, root, token
"""
    ).body[0]
    assert isinstance(mutant, ast.FunctionDef)

    violations = {value for _, _, value in _owner_violations((mutant,))}

    assert {
        "EpisodeRunRequest",
        "profile",
        "profiles",
        "policy.PolicyClient",
        "SandboxLeaseManager",
        "Path.home",
        "tempfile",
        "tempfile.mkdtemp",
        "os.environ",
        "legacy-family",
        "https://provider.invalid/v1",
    } <= violations


def test_v2_owners_are_disjoint_from_v1_profile_policy_sandbox_and_ambient_authority() -> None:
    service = _tree("service")
    api = _tree("api")

    violations = _owner_violations(
        (*_service_v2_owners(service), *_api_v2_owners(api))
    )

    assert violations == []


def test_v2_service_uses_only_the_documented_injected_lifecycle_seams() -> None:
    tree = _tree("service")
    service = _definition(tree, "BreadBoardV2EpisodeService")
    assert isinstance(service, ast.ClassDef)
    constructor = _method(tree, "BreadBoardV2EpisodeService", "__init__")
    positional = [argument.arg for argument in constructor.args.args]
    assert positional == ["self", "dependencies"]
    dependencies = _definition(tree, "V2LifecycleDependencies")
    assert isinstance(dependencies, ast.ClassDef)
    fields = {
        node.target.id
        for node in dependencies.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert fields == {
        "config_runtime",
        "runner_registry",
        "sandbox_runtime",
        "policy_client_resolver",
        "evidence_repository",
        "evidence_authority",
        "clock",
    }

    attributes = _referenced_names(service)
    required_calls = {
        "resolve_episode",
        "resolve",
        "open",
        "open_verifier",
        "reconcile_stale",
        "recover",
        "append_transition",
        "publish_completed",
        "publish_closed",
        "publish_evidence_objects",
        "prepare_export_pins",
        "scan_locators",
        "validate_plan",
        "materialize",
    }
    missing = {name for name in required_calls if not _attribute_calls(service, name)}
    assert missing == set()
    assert "PolicyRuntimeBinding" in attributes
    assert _calls(service, "PolicyRuntimeBinding")

    dependency_roots = {
        call.func.value.attr
        for call in ast.walk(service)
        if isinstance(call, ast.Call)
        and isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Attribute)
        and isinstance(call.func.value.value, ast.Attribute)
        and _qualified_name(call.func.value.value) == "self._dependencies"
    }
    assert dependency_roots == {
        "config_runtime",
        "runner_registry",
        "sandbox_runtime",
        "policy_client_resolver",
        "evidence_repository",
        "evidence_authority",
    }


def test_evidence_and_service_are_transport_free_and_do_not_infer_cleanup_from_runner_or_http() -> None:
    for module in ("evidence", "service"):
        imported = _imports(_tree(module))
        assert not any(name == "fastapi" or name.startswith("fastapi.") for name in imported)

    evidence = _tree("evidence")
    cleanup_authorities = (
        _definition(evidence, "ClosedEpisodeEnvelopeV2"),
        _method(evidence, "EpisodeEvidenceRepository", "publish_closed"),
        _definition(evidence, "_validate_cleanup_projection"),
    )
    forbidden_cleanup_sources = {
        "RunnerEvent",
        "RunnerEventLedgerV2",
        "runner_event",
        "runner_events",
        "Response",
        "HTTPException",
        "status_code",
        "http_status",
    }
    violations = [
        (owner.name, name)
        for owner in cleanup_authorities
        for name in _referenced_names(owner)
        if name.rsplit(".", 1)[-1] in forbidden_cleanup_sources
    ]
    assert violations == []

    service = _tree("service")
    finish_cleanup = _method(service, "BreadBoardV2EpisodeService", "_finish_cleanup")
    assert not (
        {"RunnerEvent", "runner_events", "Response", "HTTPException", "status_code"}
        & _referenced_names(finish_cleanup)
    )


def test_closed_construction_is_dominated_by_exact_detailed_released_receipt_validation() -> None:
    evidence = _tree("evidence")
    publish = _method(evidence, "EpisodeEvidenceRepository", "publish_closed")
    constructors = _calls(publish, "ClosedEpisodeEnvelopeV2")
    assert len(constructors) == 1
    constructor_line = constructors[0].lineno

    # The implementation spells the exact-type test as
    # `type(inputs.cleanup_receipt) is not SandboxCleanupReceipt`.
    assert any(
        isinstance(node.left, ast.Call)
        and _qualified_name(node.left.func) == "type"
        and node.left.args
        and _qualified_name(node.left.args[0]) == "inputs.cleanup_receipt"
        and any(isinstance(op, ast.IsNot) for op in node.ops)
        for node in ast.walk(publish)
        if isinstance(node, ast.Compare)
    )
    validations = _calls(publish, "_validate_cleanup_projection")
    assert validations
    assert min(call.lineno for call in validations) < constructor_line
    assert any(
        any(
            keyword.arg == "expected_lease_id"
            and _qualified_name(keyword.value) == "primary_lease_id"
            for keyword in call.keywords
        )
        and any(keyword.arg == "required_resources" for keyword in call.keywords)
        for call in validations
    )

    construction_owners = [
        owner.name
        for owner in _definitions(evidence)
        if _calls(owner, "ClosedEpisodeEnvelopeV2")
    ]
    assert construction_owners == ["EpisodeEvidenceRepository"]
    repository = _definition(evidence, "EpisodeEvidenceRepository")
    assert isinstance(repository, ast.ClassDef)
    constructor_methods = [
        node.name
        for node in repository.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and _calls(node, "ClosedEpisodeEnvelopeV2")
    ]
    assert constructor_methods == ["publish_closed", "_load_closed_envelope"]

    service = _tree("service")
    finish = _method(service, "BreadBoardV2EpisodeService", "_finish_cleanup")
    closed_calls = _attribute_calls(finish, "publish_closed")
    assert len(closed_calls) == 1
    assert _calls(finish, "_cleanup_released")
    publication_line = closed_calls[0].lineno
    assert min(call.lineno for call in _calls(finish, "_cleanup_released")) < publication_line
    released_validator = _definition(service, "_cleanup_released")
    assert isinstance(released_validator, (ast.FunctionDef, ast.AsyncFunctionDef))
    validator_names = _referenced_names(released_validator)
    assert {"CleanupState.RELEASED", "CleanupState.ALREADY_RELEASED"} <= validator_names
    assert {
        "receipt.state",
        "receipt.steps",
        "step.resource",
        "step.state",
    } <= validator_names
    validator_calls = {
        _qualified_name(node.func)
        for node in ast.walk(released_validator)
        if isinstance(node, ast.Call)
    }
    assert {"set", "all", "len"} <= validator_calls
    closed_inputs = _calls(finish, "ClosedPublicationInputsV2")
    assert len(closed_inputs) == 1
    assert any(
        keyword.arg == "cleanup_receipt" and _qualified_name(keyword.value) == "receipt"
        for keyword in closed_inputs[0].keywords
    )


def test_legacy_tombstone_is_unreachable_from_every_v2_owner() -> None:
    service = _tree("service")
    violations = [
        (owner.name, node.lineno)
        for owner in _service_v2_owners(service)
        for node in ast.walk(owner)
        if isinstance(node, ast.Name) and node.id == "_EpisodeTombstone"
    ]
    assert violations == []


def test_v2_api_has_exact_routes_no_raw_artifact_and_no_ambient_service_construction() -> None:
    api = _tree("api")
    routes = {
        route
        for node in ast.walk(api)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and (route := _route(node)) is not None
        and route.startswith("/v2")
    }
    assert routes == {
        "/v2/episodes",
        "/v2/episodes/{episode_id}:run",
        "/v2/episodes/{episode_id}",
        "/v2/episodes/{episode_id}:cancel",
        "/v2/episodes/{episode_id}/envelopes/completed",
        "/v2/episodes/{episode_id}/envelopes/closed",
        "/v2/episodes/{episode_id}/exports/{role}",
    }
    assert all("artifact" not in route.lower() for route in routes)
    assert _calls(api, "BreadBoardV2EpisodeService") == []

    create_app = _definition(api, "create_app")
    assert isinstance(create_app, (ast.FunctionDef, ast.AsyncFunctionDef))
    arguments = [*create_app.args.args, *create_app.args.kwonlyargs]
    defaults = [
        *(None for _ in range(len(create_app.args.args) - len(create_app.args.defaults))),
        *create_app.args.defaults,
        *create_app.args.kw_defaults,
    ]
    parameters = dict(zip((argument.arg for argument in arguments), defaults, strict=True))
    assert parameters["v2_service"] is None

    v2_owners = _api_v2_owners(api)
    ambient = [
        (owner.name, node.lineno, _qualified_name(node))
        for owner in v2_owners
        for node in ast.walk(owner)
        if isinstance(node, (ast.Name, ast.Attribute))
        and _qualified_name(node) in AMBIENT_NAMES
    ]
    assert ambient == []


def test_v2_api_exports_only_through_evidence_gated_service_seam() -> None:
    api = _tree("api")
    export_owner = _export_route_owner(api)
    assert len(_calls(export_owner, "ExportAuthorizationClaimsV2")) == 1
    assert _calls(export_owner, "ExportAuthorizationV2") == []
    assert _attribute_calls(export_owner, "export_closed")
    assert not _attribute_calls(export_owner, "get_bytes")
    assert not _attribute_calls(export_owner, "get_artifact")

    service = _tree("service")
    export = _method(service, "BreadBoardV2EpisodeService", "export_closed")
    calls = _attribute_calls(export, "export_closed_claims")
    assert len(calls) == 1
    assert _qualified_name(calls[0].func.value) == "self._dependencies.evidence_repository"

    evidence = _tree("evidence")
    select = _method(evidence, "EpisodeEvidenceRepository", "export_closed_claims")
    assert _attribute_calls(select, "_load_export_authorization")
    assert _calls(select, "len")
    assert len(_attribute_calls(select, "_export_with_pinned_authorization")) == 1


def test_closure_authority_scanner_rejects_every_forbidden_source_mutant() -> None:
    mutants = (
        (
            "workspace_file_artifact_fabrication",
            """
class Service:
    def publish(self):
        return EvidenceObjectInputV2(workspace.read_bytes())
""",
            "def export(role = Header(alias='X-BreadBoard-Export-Scope')): pass",
        ),
        (
            "unvalidated_evidence_authority",
            """
class Service:
    def publish(self):
        return other_authority.materialize(plan)
""",
            "def export(role = Header(alias='X-BreadBoard-Export-Scope')): pass",
        ),
        (
            "external_closed_transition_append",
            """
class Service:
    def close(self):
        event = LifecycleEventV2(to_state=EpisodeLifecycleState.CLOSED.value)
        repository.append_transition(event)
""",
            "def export(role = Header(alias='X-BreadBoard-Export-Scope')): pass",
        ),
        (
            "ambient_export_default",
            "class Service: pass",
            """
def export(
    subject = Header(default=None, alias="X-BreadBoard-Export-Subject-Digest"),
    not_after = Header(default=None, alias="X-BreadBoard-Export-Not-After"),
):
    pass
""",
        ),
        (
            "api_export_authorization_construction",
            "class Service: pass",
            """
def export():
    return ExportAuthorizationV2(subject="caller")
""",
        ),
        (
            "caller_controlled_export_window",
            "class Service: pass",
            """
def export(not_before = Header(alias="X-BreadBoard-Export-Not-Before")):
    pass
""",
        ),
    )
    for expected, service_source, export_source in mutants:
        service_owner = ast.parse(service_source).body[0]
        export_owner = ast.parse(export_source).body[0]
        assert isinstance(export_owner, (ast.FunctionDef, ast.AsyncFunctionDef))
        assert expected in _closure_authority_violations(service_owner, export_owner)


def test_v2_closure_has_no_workspace_fabrication_unvalidated_authority_external_closed_append_or_ambient_export_defaults() -> None:
    service = _tree("service")
    service_owner = _definition(service, "BreadBoardV2EpisodeService")
    api = _tree("api")
    assert _closure_authority_violations(
        service_owner, _export_route_owner(api)
    ) == set()


def test_evidence_authority_owns_typed_bytes_without_workspace_or_artifact_refs() -> None:
    evidence = _tree("evidence")
    source = _definition(evidence, "EvidenceRoleSourceV2")
    assert isinstance(source, ast.ClassDef)
    source_values = {
        node.targets[0].id: node.value.value
        for node in source.body
        if isinstance(node, ast.Assign)
        and len(node.targets) == 1
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
    }
    assert source_values == {
        "RUNNER_RESULT": "runner_result",
        "VERIFIER_SNAPSHOT_RECEIPT": "verifier_snapshot_receipt",
        "VERIFIER_RESULT": "verifier_result",
    }

    object_input = _definition(evidence, "EvidenceObjectInputV2")
    assert isinstance(object_input, ast.ClassDef)
    fields = {
        node.target.id: ast.unparse(node.annotation)
        for node in object_input.body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert fields == {
        "role": "str",
        "source": "EvidenceRoleSourceV2",
        "producer_id": "str",
        "producer_implementation_digest": "str",
        "payload": "bytes",
        "media_type": "str",
        "parent_digests": "tuple[str, ...]",
    }
    rendered = ast.unparse(object_input)
    assert "ArtifactRef" not in rendered
    assert not any(
        token in rendered
        for token in ("workspace_path", "source_path", "file_path", "Path(")
    )

    authority = _definition(evidence, "V2EvidenceAuthority")
    assert isinstance(authority, ast.ClassDef)
    assert {node.name for node in authority.body if isinstance(node, ast.FunctionDef)} >= {
        "validate_plan",
        "materialize",
    }
    service = _tree("service")
    create = _method(service, "BreadBoardV2EpisodeService", "_create_fresh")
    publish = _method(service, "BreadBoardV2EpisodeService", "_publish_completed")
    assert len(_attribute_calls(create, "validate_plan")) == 1
    assert len(_attribute_calls(publish, "materialize")) == 1
    assert len(_attribute_calls(publish, "publish_evidence_objects")) == 1
