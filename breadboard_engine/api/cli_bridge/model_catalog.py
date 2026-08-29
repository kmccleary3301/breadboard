"""Deterministic configured-only model catalog projection."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from ...provider.routing import ProviderRouteError, provider_router
from ...provider_broker.catalog import (
    get_provider_catalog_entry,
    get_provider_catalog_entry_for_adapter,
)
from ...security import redaction
from .models import ModelCatalogEntry, ModelCatalogIssue

CredentialOriginResolver = Callable[[str], Mapping[str, str] | None]


def _configured_value(entry: Any) -> tuple[str | None, Mapping[str, Any]]:
    if isinstance(entry, str):
        return entry, {}
    if not isinstance(entry, Mapping):
        return None, {}
    model_id = entry.get("id") or entry.get("model_id") or entry.get("model")
    return (model_id if isinstance(model_id, str) else None), entry


def _optional_string(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _safe_mapping(value: Any, *, path: str) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    scrubbed, _problems = redaction.scrub_structure(dict(value), path=path)
    return dict(scrubbed) if isinstance(scrubbed, Mapping) else None


def _entry_fields(model_id: str, value: Mapping[str, Any]) -> dict[str, Any]:
    context_length = (
        value.get("context_length")
        or value.get("contextLength")
        or value.get("context_tokens")
    )
    if isinstance(context_length, bool) or not isinstance(context_length, (int, float)):
        context_length = None
    else:
        context_length = int(context_length)
    params = value.get("params") or value.get("parameters")
    routing = value.get("routing")
    metadata = value.get("metadata")
    return {
        "adapter": _optional_string(value.get("adapter") or value.get("adapter_id")),
        "name": _optional_string(value.get("name")) or model_id,
        "context_length": context_length,
        "params": _safe_mapping(params, path="$.model.params"),
        "routing": _safe_mapping(routing, path="$.model.routing"),
        "metadata": _safe_mapping(metadata, path="$.model.metadata"),
    }


def _unavailable_entry(
    model_id: str,
    value: Mapping[str, Any],
    *,
    provider_id: str | None,
    canonical_provider: str | None,
    support_tier: str,
    reason: str,
) -> ModelCatalogEntry:
    return ModelCatalogEntry(
        id=model_id,
        provider=provider_id,
        canonical_provider=canonical_provider,
        support_tier=support_tier,
        available=False,
        availability_reason=reason,
        discovery="configured_only",
        source="configured",
        **_entry_fields(model_id, value),
    )


def build_model_catalog(
    configured_models: Sequence[Any],
    dynamic_models: Sequence[Any] = (),
    *,
    credential_origin: CredentialOriginResolver | None = None,
) -> tuple[list[ModelCatalogEntry], list[ModelCatalogIssue]]:
    """Project validated config rows; reject dynamic widening under configured-only policy."""
    origin_for = credential_origin or provider_router.get_credential_origin
    models: list[ModelCatalogEntry] = []
    issues: list[ModelCatalogIssue] = []
    seen: set[str] = set()
    seen_routes: set[tuple[str, str]] = set()

    for index, candidate in enumerate(configured_models):
        model_id, value = _configured_value(candidate)
        if model_id is None or not model_id or model_id != model_id.strip():
            issues.append(
                ModelCatalogIssue(
                    code="invalid_model", source="configured", index=index
                )
            )
            continue
        if model_id in seen:
            issues.append(
                ModelCatalogIssue(
                    code="duplicate_model",
                    model_id=model_id,
                    source="configured",
                    index=index,
                )
            )
            continue
        seen.add(model_id)

        provider_hint = _optional_string(value.get("provider"))
        adapter_hint = _optional_string(value.get("adapter") or value.get("adapter_id"))
        raw_provider = value.get("provider")
        raw_adapter = (
            value.get("adapter") if "adapter" in value else value.get("adapter_id")
        )
        if (raw_provider is not None and provider_hint is None) or (
            raw_adapter is not None and adapter_hint is None
        ):
            issues.append(
                ModelCatalogIssue(
                    code="invalid_model",
                    model_id=model_id,
                    source="configured",
                    index=index,
                )
            )
            continue
        route_id = model_id
        leading_provider = (
            get_provider_catalog_entry(model_id.split("/", 1)[0])
            if "/" in model_id
            else None
        )
        effective_provider_hint = provider_hint
        if (
            effective_provider_hint is None
            and leading_provider is None
            and "/" not in model_id
            and adapter_hint
        ):
            adapter_entry = get_provider_catalog_entry_for_adapter(adapter_hint)
            if adapter_entry is None:
                issues.append(
                    ModelCatalogIssue(
                        code="invalid_model",
                        model_id=model_id,
                        source="configured",
                        index=index,
                    )
                )
                continue
            effective_provider_hint = adapter_entry.provider_id
        if effective_provider_hint and leading_provider is None:
            route_id = f"{effective_provider_hint}/{model_id}"
        try:
            canonical_provider, native_model, _route_kind = (
                provider_router.parse_model_id(route_id)
            )
        except ProviderRouteError as exc:
            if exc.code == "invalid_model_id":
                issues.append(
                    ModelCatalogIssue(
                        code="invalid_model",
                        model_id=model_id,
                        provider_id=exc.provider_id or effective_provider_hint,
                        source="configured",
                        index=index,
                    )
                )
                continue
            entry = get_provider_catalog_entry(
                exc.provider_id or effective_provider_hint or model_id.split("/", 1)[0]
            )
            deferred = entry is not None and entry.support_tier == "deferred"
            reason = "deferred_provider" if deferred else "unsupported_provider"
            provider_id = (
                entry.provider_id
                if entry is not None
                else exc.provider_id
                or effective_provider_hint
                or model_id.split("/", 1)[0]
            )
            issues.append(
                ModelCatalogIssue(
                    code=reason,
                    model_id=model_id,
                    provider_id=provider_id,
                    source="configured",
                    index=index,
                )
            )
            models.append(
                _unavailable_entry(
                    model_id,
                    value,
                    provider_id=provider_id,
                    canonical_provider=entry.provider_id if entry else None,
                    support_tier="deferred" if deferred else "unsupported",
                    reason=reason,
                )
            )
            continue

        if provider_hint:
            hinted = get_provider_catalog_entry(provider_hint)
            if hinted is None or hinted.provider_id != canonical_provider:
                issues.append(
                    ModelCatalogIssue(
                        code="invalid_model",
                        model_id=model_id,
                        provider_id=provider_hint,
                        source="configured",
                        index=index,
                    )
                )
                continue
        route_key = (canonical_provider, native_model)
        if route_key in seen_routes:
            issues.append(
                ModelCatalogIssue(
                    code="duplicate_model",
                    model_id=model_id,
                    provider_id=canonical_provider,
                    source="configured",
                    index=index,
                )
            )
            continue
        seen_routes.add(route_key)
        entry = get_provider_catalog_entry(canonical_provider)
        if entry is None:
            raise AssertionError("router returned an uncataloged provider")
        if entry.support_tier == "evidence":
            available = True
            reason = None
        elif entry.auth_owner == "provider":
            available = True
            reason = "provider_managed"
        else:
            available = origin_for(route_id) is not None
            reason = None if available else "missing_auth"
        models.append(
            ModelCatalogEntry(
                id=model_id,
                provider=canonical_provider,
                canonical_provider=canonical_provider,
                support_tier=entry.support_tier,
                available=available,
                availability_reason=reason,
                discovery="configured_only",
                source="configured",
                **_entry_fields(model_id, value),
            )
        )

    for index, candidate in enumerate(dynamic_models):
        model_id, value = _configured_value(candidate)
        provider_id = _optional_string(value.get("provider"))
        if provider_id is None and model_id and "/" in model_id:
            provider_id = model_id.split("/", 1)[0]
        issues.append(
            ModelCatalogIssue(
                code="stale_dynamic_catalog",
                model_id=model_id,
                provider_id=provider_id,
                source="dynamic",
                index=index,
            )
        )

    return models, issues


__all__ = ["build_model_catalog"]
