"""Translate an effective model-role binding into provider-native request options."""

from __future__ import annotations

from typing import Any, Mapping

from .contracts import ProviderRuntimeContext, ProviderRuntimeError


def _active_binding(context: ProviderRuntimeContext) -> Mapping[str, Any] | None:
    config = context.agent_config
    lock = config.get("model_role_lock") if isinstance(config, Mapping) else None
    if not isinstance(lock, Mapping):
        return None
    roles = lock.get("roles")
    if not isinstance(roles, Mapping):
        return None
    defaults = lock.get("defaults")
    default_role = defaults.get("role") if isinstance(defaults, Mapping) else None
    role = str(config.get("active_model_role") or default_role or "").strip()
    binding = roles.get(role)
    if not role or not isinstance(binding, Mapping):
        raise ProviderRuntimeError(
            "Active model role has no effective binding",
            kind="configuration",
            details={"code": "invalid_active_model_role"},
        )
    try:
        context.session_state.set_provider_metadata("active_model_role", role)
        context.session_state.set_provider_metadata(
            "active_model_role_policy",
            {
                key: binding[key]
                for key in ("reasoning", "generation", "service_tier")
                if key in binding
            },
        )
    except Exception:
        pass
    return binding


def _unsupported(message: str, code: str) -> None:
    raise ProviderRuntimeError(
        message,
        kind="configuration",
        details={"code": code},
    )


def _generation(binding: Mapping[str, Any] | None) -> dict[str, Any]:
    if binding is None:
        return {}
    value = binding.get("generation")
    return dict(value) if isinstance(value, Mapping) else {}


def _reasoning(binding: Mapping[str, Any] | None) -> Mapping[str, Any]:
    if binding is None:
        return {}
    value = binding.get("reasoning")
    return value if isinstance(value, Mapping) else {}


def openai_chat_role_options(
    context: ProviderRuntimeContext,
    *,
    provider_id: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return top-level Chat Completions kwargs and extra-body options."""

    binding = _active_binding(context)
    generation = _generation(binding)
    request: dict[str, Any] = {}
    for source, target in (
        ("temperature", "temperature"),
        ("top_p", "top_p"),
        ("max_output_tokens", "max_completion_tokens"),
        ("seed", "seed"),
    ):
        if source in generation:
            request[target] = generation[source]
    if binding is not None and binding.get("service_tier") not in (None, "default"):
        request["service_tier"] = binding["service_tier"]

    reasoning = _reasoning(binding)
    mode = reasoning.get("mode", "inherit")
    if mode == "budget":
        _unsupported(
            "OpenAI-compatible Chat Completions does not accept a reasoning token budget",
            "unsupported_role_reasoning_budget",
        )
    effort = None
    if mode == "disabled":
        effort = "none"
    elif mode == "effort":
        effort = reasoning.get("effort")
    elif mode != "inherit":
        _unsupported(
            "Unsupported model-role reasoning mode", "unsupported_role_reasoning_mode"
        )
    if effort is None:
        return request, {}
    if provider_id == "openrouter":
        return request, {"reasoning": {"effort": effort}}
    request["reasoning_effort"] = effort
    return request, {}


def openai_responses_role_options(context: ProviderRuntimeContext) -> dict[str, Any]:
    """Return exact Responses API options for the active role."""

    binding = _active_binding(context)
    request = _generation(binding)
    if "seed" in request:
        _unsupported(
            "OpenAI Responses does not accept a seed",
            "unsupported_role_generation_seed",
        )
    if binding is not None and binding.get("service_tier") not in (None, "default"):
        request["service_tier"] = binding["service_tier"]
    reasoning = _reasoning(binding)
    mode = reasoning.get("mode", "inherit")
    if mode in {"disabled", "effort"} and any(
        field in request for field in ("temperature", "top_p")
    ):
        _unsupported(
            "OpenAI reasoning does not accept sampling overrides",
            "unsupported_role_generation_sampling_with_reasoning",
        )
    if mode == "budget":
        _unsupported(
            "OpenAI Responses does not accept a reasoning token budget",
            "unsupported_role_reasoning_budget",
        )
    if mode == "disabled":
        request["reasoning"] = {"effort": "none"}
    elif mode == "effort":
        request["reasoning"] = {"effort": reasoning.get("effort")}
    elif mode != "inherit":
        _unsupported(
            "Unsupported model-role reasoning mode", "unsupported_role_reasoning_mode"
        )
    if reasoning.get("expose_summary") is True:
        request.setdefault("reasoning", {})["summary"] = "auto"
    return request


def anthropic_role_options(
    context: ProviderRuntimeContext,
    *,
    default_max_output_tokens: int | None = None,
    base_sampling: bool = False,
) -> dict[str, Any]:
    """Return exact Anthropic Messages options for the active role."""

    binding = _active_binding(context)
    generation = _generation(binding)
    if "seed" in generation:
        _unsupported(
            "Anthropic Messages does not accept a seed",
            "unsupported_role_generation_seed",
        )
    request = {
        target: generation[source]
        for source, target in (
            ("temperature", "temperature"),
            ("top_p", "top_p"),
            ("max_output_tokens", "max_tokens"),
        )
        if source in generation
    }
    if binding is not None and binding.get("service_tier") not in (None, "default"):
        _unsupported(
            "Anthropic Messages does not accept this service tier",
            "unsupported_role_service_tier",
        )
    reasoning = _reasoning(binding)
    mode = reasoning.get("mode", "inherit")
    if mode in {"budget", "effort"} and (
        base_sampling
        or any(field in request for field in ("temperature", "top_p"))
    ):
        _unsupported(
            "Anthropic thinking does not accept sampling overrides",
            "unsupported_role_generation_sampling_with_thinking",
        )
    if mode == "disabled":
        request["thinking"] = {"type": "disabled"}
    elif mode == "budget":
        budget_tokens = reasoning.get("budget_tokens")
        max_tokens = request.get("max_tokens", default_max_output_tokens)
        if (
            type(budget_tokens) is not int
            or budget_tokens < 1024
            or type(max_tokens) is not int
            or budget_tokens >= max_tokens
        ):
            _unsupported(
                "Anthropic thinking budget must be at least 1024 and below max_tokens",
                "unsupported_role_reasoning_budget",
            )
        request["thinking"] = {
            "type": "enabled",
            "budget_tokens": budget_tokens,
        }
    elif mode == "effort":
        effort = reasoning.get("effort")
        if effort == "minimal":
            _unsupported(
                "Anthropic adaptive reasoning does not accept minimal effort",
                "unsupported_role_reasoning_effort",
            )
        request["thinking"] = {"type": "adaptive"}
        request["output_config"] = {"effort": effort}
    elif mode != "inherit":
        _unsupported(
            "Unsupported model-role reasoning mode", "unsupported_role_reasoning_mode"
        )
    return request


def codex_role_options(context: ProviderRuntimeContext) -> dict[str, Any]:
    """Return Codex app-server turn/start overrides for the active role."""

    binding = _active_binding(context)
    if _generation(binding):
        _unsupported(
            "Codex app-server does not expose model-role generation sampling overrides",
            "unsupported_role_generation",
        )
    request: dict[str, Any] = {}
    if binding is not None and binding.get("service_tier") not in (None, "default"):
        request["serviceTier"] = binding["service_tier"]
    reasoning = _reasoning(binding)
    mode = reasoning.get("mode", "inherit")
    if mode == "budget":
        _unsupported(
            "Codex app-server does not accept a reasoning token budget",
            "unsupported_role_reasoning_budget",
        )
    if mode == "disabled":
        request["effort"] = "none"
    elif mode == "effort":
        request["effort"] = reasoning.get("effort")
    elif mode != "inherit":
        _unsupported(
            "Unsupported model-role reasoning mode", "unsupported_role_reasoning_mode"
        )
    if reasoning.get("expose_summary") is not None:
        request["summary"] = "auto" if reasoning["expose_summary"] else "none"
    return request
