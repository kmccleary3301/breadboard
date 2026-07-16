from collections.abc import Mapping

from .extensions import CompositionError, Contribution, Operations, _owned

_ROOTS = frozenset({"providers", "provider_tools"})


def _validate(document: Mapping[str, object]) -> None:
    providers = document.get("providers")
    if not isinstance(providers, Mapping):
        return
    models = providers.get("models")
    if not isinstance(models, (list, tuple)):
        return
    graph: dict[str, tuple[str, ...]] = {}
    for model in models:
        if not isinstance(model, Mapping):
            raise CompositionError("provider models must be mappings")
        identifier = model.get("id")
        if not isinstance(identifier, str) or not identifier:
            raise CompositionError("provider model IDs must be nonempty strings")
        if identifier in graph:
            raise CompositionError("provider model IDs must be unique")
        routing = model.get("routing")
        fallbacks = ()
        if isinstance(routing, Mapping):
            fallbacks = routing.get("fallback_models", ())
        if not isinstance(fallbacks, (list, tuple)):
            fallbacks = ()
        if any(not isinstance(target, str) or not target for target in fallbacks):
            raise CompositionError("provider fallback targets must be nonempty strings")
        if len(fallbacks) != len(set(fallbacks)):
            message = f"provider fallbacks for {identifier} must be distinct"
            raise CompositionError(message)
        graph[identifier] = tuple(fallbacks)
    declared = set(graph)
    default = providers.get("default_model")
    if default not in declared:
        raise CompositionError("providers.default_model must name a declared model")
    for identifier, fallbacks in graph.items():
        if identifier in fallbacks:
            raise CompositionError(f"provider {identifier} cannot fall back to itself")
        if any(target not in declared for target in fallbacks):
            raise CompositionError(f"provider {identifier} has an undeclared fallback")
    state: dict[str, int] = {}
    for root in graph:
        stack = [(root, False)]
        while stack:
            identifier, exiting = stack.pop()
            if exiting:
                state[identifier] = 2
            elif state.get(identifier) == 1:
                raise CompositionError("provider fallback graph must be acyclic")
            elif state.get(identifier) != 2:
                state[identifier] = 1
                stack.append((identifier, True))
                stack.extend((target, False) for target in reversed(graph[identifier]))


def build_provider_module(operations: Operations, precedence: int = 10) -> Contribution:
    return _owned("provider", precedence, operations, _ROOTS, _validate)
