"""Composable provider and provider-tool settings."""

from collections.abc import Mapping, Sequence

from .extensions import CompositionError, ModuleContribution, Operation, contribution

_ROOTS = frozenset({"providers", "provider_tools"})


def _validate(document: Mapping[str, object]) -> None:
    providers = document.get("providers")
    if not isinstance(providers, Mapping):
        return
    models = providers.get("models")
    if not isinstance(models, (list, tuple)):
        return
    identifiers: list[str] = []
    graph: dict[str, tuple[str, ...]] = {}
    for model in models:
        if not isinstance(model, Mapping):
            raise CompositionError("provider models must be mappings")
        identifier = model.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise CompositionError("provider model IDs must be nonempty strings")
        identifiers.append(identifier)
        routing = model.get("routing")
        fallbacks = (
            routing.get("fallback_models") if isinstance(routing, Mapping) else None
        )
        if not isinstance(fallbacks, (list, tuple)):
            graph[identifier] = ()
            continue
        if any(not isinstance(target, str) or not target for target in fallbacks):
            raise CompositionError("provider fallback targets must be nonempty strings")
        if len(fallbacks) != len(set(fallbacks)):
            raise CompositionError(
                f"provider fallbacks for {identifier} must be distinct"
            )
        graph[identifier] = tuple(fallbacks)
    if len(identifiers) != len(set(identifiers)):
        raise CompositionError("provider model IDs must be unique")
    declared = set(identifiers)
    default = providers.get("default_model")
    if default not in declared:
        raise CompositionError("providers.default_model must name a declared model")
    for identifier, fallbacks in graph.items():
        if identifier in fallbacks:
            raise CompositionError(f"provider {identifier} cannot fall back to itself")
        if any(target not in declared for target in fallbacks):
            raise CompositionError(f"provider {identifier} has an undeclared fallback")
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(identifier: str) -> None:
        if identifier in visiting:
            raise CompositionError("provider fallback graph must be acyclic")
        if identifier in visited:
            return
        visiting.add(identifier)
        for target in graph.get(identifier, ()):
            visit(target)
        visiting.remove(identifier)
        visited.add(identifier)

    for identifier in identifiers:
        visit(identifier)


def build_provider_module(
    operations: Sequence[Operation], precedence: int = 10
) -> ModuleContribution:
    if any(
        not operation.path or operation.path[0] not in _ROOTS
        for operation in operations
    ):
        raise CompositionError(
            "provider modules may only target providers or provider_tools"
        )
    return contribution("provider", precedence, operations, _validate)
