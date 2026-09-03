"""Data-driven provider and authentication-flow catalog.

The catalog owns provider metadata; flow adapters consume the exact endpoint
values recorded in the source-established provider specifications. Adding an
OpenAI-compatible provider is therefore a data entry, not a new runtime class.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ProviderCapabilities:
    tool_calls: str  # e.g., "parallel", "sequential"
    streaming: str  # e.g., "text_deltas", "event_deltas", "none"
    json_mode: str  # e.g., "strict", "best_effort", "none"
    reasoning: str  # e.g., "encrypted", "summary", "none"
    caching: str  # e.g., "explicit", "implicit", "none"


@dataclass(frozen=True)
class OAuthFlowSpec:
    flow_id: str
    auth_url: str
    token_url: str
    client_id: str | None
    scopes: tuple[str, ...]
    callback_port: int
    callback_path: str
    client_id_env: str | None = None
    device_usercode_url: str | None = None
    device_token_url: str | None = None
    device_redirect_uri: str | None = None
    device_auth_url: str | None = None
    store_provider_id: str | None = None

    def resolved_client_id(self) -> str | None:
        if self.client_id:
            return self.client_id
        value = os.environ.get(self.client_id_env, "") if self.client_id_env else ""
        return value.strip() or None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "flow_id": self.flow_id,
            "kind": "oauth2",
            "auth_url": self.auth_url,
            "token_url": self.token_url,
            "client_id_configured": self.resolved_client_id() is not None,
            "scopes": list(self.scopes),
            "callback_port": self.callback_port,
            "callback_path": self.callback_path,
        }
        if self.device_usercode_url:
            result["device"] = {
                "usercode_url": self.device_usercode_url,
                "token_url": self.device_token_url,
                "redirect_uri": self.device_redirect_uri,
                "auth_url": self.device_auth_url,
            }
        return result


@dataclass(frozen=True)
class ProviderCatalogEntry:
    provider_id: str
    display_name: str
    runtime_id: str
    auth_schemes: tuple[str, ...] = ()
    oauth_flows: tuple[OAuthFlowSpec, ...] = ()
    compatible_protocol: str = "openai"
    base_url: str | None = None
    aliases: tuple[str, ...] = ()
    config_adapter_ids: tuple[str, ...] = ()
    support_tier: Literal["core", "deferred", "evidence"] = "evidence"
    auth_owner: Literal["broker", "provider", "none"] = "none"
    api_key_env: str | None = None
    default_api_variant: str = "chat"
    model_discovery: Literal["configured_only"] = "configured_only"
    supports_native_tools: bool = True
    supports_streaming: bool = True
    supports_reasoning_traces: bool = False
    supports_cache_control: bool = False
    capabilities: ProviderCapabilities | None = None
    tool_adapter_kind: str = "openai"
    credential_required: bool = False

    def as_view(
        self,
        *,
        available: bool | None = None,
        availability_reason: str | None = None,
    ) -> dict[str, Any]:
        product_oauth_flows = self.oauth_flows if self.auth_owner == "broker" else ()
        return {
            "provider_id": self.provider_id,
            "aliases": list(self.aliases),
            "display_name": self.display_name,
            "support_tier": self.support_tier,
            "auth_owner": self.auth_owner,
            "auth_schemes": list(self.auth_schemes),
            "available": bool(available),
            "availability_reason": availability_reason,
            "login_available": any(
                flow.resolved_client_id() is not None for flow in product_oauth_flows
            ),
            "oauth_flows": [flow.flow_id for flow in product_oauth_flows],
            "model_discovery": self.model_discovery,
            "runtime_id": self.runtime_id,
            "compatible_protocol": self.compatible_protocol,
            "base_url": self.base_url,
        }


OPENAI_CODEX_OAUTH = OAuthFlowSpec(
    flow_id="openai-codex",
    auth_url="https://auth.openai.com/oauth/authorize",
    token_url="https://auth.openai.com/oauth/token",
    client_id="app_EMoamEEZ73f0CkXaXp7hrann",
    scopes=(
        "openid",
        "profile",
        "email",
        "offline_access",
        "api.connectors.read",
        "api.connectors.invoke",
    ),
    callback_port=1455,
    callback_path="/auth/callback",
    device_usercode_url="https://auth.openai.com/api/accounts/deviceauth/usercode",
    device_token_url="https://auth.openai.com/api/accounts/deviceauth/token",
    device_redirect_uri="https://auth.openai.com/deviceauth/callback",
    device_auth_url="https://auth.openai.com/codex/device",
    store_provider_id="codex",
)

ANTHROPIC_OAUTH = OAuthFlowSpec(
    flow_id="anthropic",
    auth_url="https://claude.ai/oauth/authorize",
    token_url="https://api.anthropic.com/v1/oauth/token",
    client_id="9d1c250a-e61b-44d9-88ed-5944d1962f5e",
    scopes=(
        "org:create_api_key",
        "user:profile",
        "user:inference",
        "user:sessions:claude_code",
        "user:mcp_servers",
        "user:file_upload",
    ),
    callback_port=54545,
    callback_path="/callback",
)

GOOGLE_GEMINI_CLI_OAUTH = OAuthFlowSpec(
    flow_id="google-gemini-cli",
    auth_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    client_id=None,
    client_id_env="BREADBOARD_GOOGLE_GEMINI_CLI_OAUTH_CLIENT_ID",
    scopes=(
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
    ),
    callback_port=8085,
    callback_path="/oauth2callback",
)

GOOGLE_ANTIGRAVITY_OAUTH = OAuthFlowSpec(
    flow_id="google-antigravity",
    auth_url="https://accounts.google.com/o/oauth2/v2/auth",
    token_url="https://oauth2.googleapis.com/token",
    client_id=None,
    client_id_env="BREADBOARD_GOOGLE_ANTIGRAVITY_OAUTH_CLIENT_ID",
    scopes=(
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/cclog",
        "https://www.googleapis.com/auth/experimentsandconfigs",
    ),
    callback_port=51121,
    callback_path="/oauth-callback",
)


_PROVIDER_DEFINITIONS: tuple[ProviderCatalogEntry, ...] = (
    ProviderCatalogEntry(
        provider_id="codex",
        aliases=("openai-codex",),
        display_name="Codex",
        runtime_id="codex_app_server",
        config_adapter_ids=("codex_app_server",),
        auth_schemes=("provider_managed",),
        oauth_flows=(OPENAI_CODEX_OAUTH,),
        support_tier="core",
        auth_owner="provider",
        default_api_variant="app_server",
        supports_native_tools=False,
        supports_reasoning_traces=True,
        capabilities=ProviderCapabilities(
            tool_calls="parallel",
            streaming="event_deltas",
            json_mode="best_effort",
            reasoning="summary",
            caching="implicit",
        ),
        tool_adapter_kind="openai",
        credential_required=False,
    ),
    ProviderCatalogEntry(
        provider_id="openai",
        display_name="OpenAI",
        runtime_id="openai_chat",
        config_adapter_ids=("openai", "openai_chat", "openai_responses", "responses"),
        auth_schemes=("api_key",),
        support_tier="core",
        auth_owner="broker",
        api_key_env="OPENAI_API_KEY",
        supports_reasoning_traces=True,
        capabilities=ProviderCapabilities(
            tool_calls="parallel",
            streaming="text_deltas",
            json_mode="strict",
            reasoning="encrypted",
            caching="implicit",
        ),
        tool_adapter_kind="openai",
        credential_required=True,
    ),
    ProviderCatalogEntry(
        provider_id="anthropic",
        display_name="Anthropic",
        runtime_id="anthropic_messages",
        config_adapter_ids=("anthropic", "anthropic_messages"),
        auth_schemes=("api_key", "oauth2"),
        oauth_flows=(ANTHROPIC_OAUTH,),
        compatible_protocol="anthropic",
        support_tier="core",
        auth_owner="broker",
        api_key_env="ANTHROPIC_API_KEY",
        default_api_variant="messages",
        supports_reasoning_traces=True,
        supports_cache_control=True,
        capabilities=ProviderCapabilities(
            tool_calls="parallel",
            streaming="event_deltas",
            json_mode="best_effort",
            reasoning="summary",
            caching="explicit",
        ),
        tool_adapter_kind="anthropic",
        credential_required=True,
    ),
    ProviderCatalogEntry(
        provider_id="openrouter",
        display_name="OpenRouter",
        runtime_id="openrouter_chat",
        config_adapter_ids=("openrouter_chat",),
        auth_schemes=("api_key",),
        base_url="https://openrouter.ai/api/v1",
        support_tier="core",
        auth_owner="broker",
        api_key_env="OPENROUTER_API_KEY",
        supports_reasoning_traces=True,
        capabilities=ProviderCapabilities(
            tool_calls="parallel",
            streaming="event_deltas",
            json_mode="best_effort",
            reasoning="summary",
            caching="none",
        ),
        tool_adapter_kind="openrouter",
        credential_required=True,
    ),
    ProviderCatalogEntry(
        provider_id="google-gemini-cli",
        display_name="Google Cloud Code Assist (Gemini CLI)",
        runtime_id="openai_chat",
        auth_schemes=("oauth2",),
        oauth_flows=(GOOGLE_GEMINI_CLI_OAUTH,),
        support_tier="deferred",
        auth_owner="broker",
        capabilities=ProviderCapabilities(
            tool_calls="parallel",
            streaming="text_deltas",
            json_mode="strict",
            reasoning="encrypted",
            caching="implicit",
        ),
        tool_adapter_kind="openai",
        credential_required=True,
    ),
    ProviderCatalogEntry(
        provider_id="google-antigravity",
        display_name="Antigravity",
        runtime_id="openai_chat",
        auth_schemes=("oauth2",),
        oauth_flows=(GOOGLE_ANTIGRAVITY_OAUTH,),
        support_tier="deferred",
        auth_owner="broker",
        capabilities=ProviderCapabilities(
            tool_calls="parallel",
            streaming="text_deltas",
            json_mode="strict",
            reasoning="encrypted",
            caching="implicit",
        ),
        tool_adapter_kind="openai",
        credential_required=True,
    ),
    ProviderCatalogEntry(
        provider_id="mock",
        display_name="Mock",
        runtime_id="mock_chat",
        default_api_variant="mock",
        supports_native_tools=False,
        supports_streaming=False,
        capabilities=ProviderCapabilities(
            tool_calls="sequential",
            streaming="none",
            json_mode="strict",
            reasoning="none",
            caching="none",
        ),
        tool_adapter_kind="openai",
        credential_required=False,
    ),
    ProviderCatalogEntry(
        provider_id="cli_mock",
        display_name="CLI Mock",
        runtime_id="cli_mock_chat",
        config_adapter_ids=("cli_mock_chat",),
        default_api_variant="mock",
        supports_native_tools=False,
        supports_streaming=False,
        capabilities=ProviderCapabilities(
            tool_calls="sequential",
            streaming="none",
            json_mode="strict",
            reasoning="none",
            caching="none",
        ),
        tool_adapter_kind="openai",
        credential_required=False,
    ),
    ProviderCatalogEntry(
        provider_id="smoke",
        display_name="Smoke",
        runtime_id="smoke_chat",
        config_adapter_ids=("smoke_chat",),
        default_api_variant="mock",
        supports_native_tools=False,
        supports_streaming=False,
        capabilities=ProviderCapabilities(
            tool_calls="sequential",
            streaming="none",
            json_mode="strict",
            reasoning="none",
            caching="none",
        ),
        tool_adapter_kind="openai",
        credential_required=False,
    ),
    ProviderCatalogEntry(
        provider_id="replay",
        display_name="Replay",
        runtime_id="replay",
        config_adapter_ids=("replay",),
        default_api_variant="replay",
        supports_streaming=False,
        supports_reasoning_traces=True,
        capabilities=ProviderCapabilities(
            tool_calls="parallel",
            streaming="none",
            json_mode="strict",
            reasoning="summary",
            caching="none",
        ),
        tool_adapter_kind="openai",
        credential_required=False,
    ),
)

_PROVIDER_CATALOG: dict[str, ProviderCatalogEntry] = {
    entry.provider_id: entry for entry in _PROVIDER_DEFINITIONS
}



_CONFIG_ADAPTER_CATALOG: dict[str, ProviderCatalogEntry] = {}
for _entry in _PROVIDER_CATALOG.values():
    for _adapter_id in _entry.config_adapter_ids:
        if _adapter_id in _CONFIG_ADAPTER_CATALOG:
            raise RuntimeError(f"duplicate provider config adapter: {_adapter_id}")
        _CONFIG_ADAPTER_CATALOG[_adapter_id] = _entry


def provider_catalog() -> tuple[ProviderCatalogEntry, ...]:
    return tuple(_PROVIDER_CATALOG.values())


def product_provider_catalog() -> tuple[ProviderCatalogEntry, ...]:
    return tuple(
        entry for entry in _PROVIDER_CATALOG.values() if entry.support_tier == "core"
    )


def routable_provider_catalog() -> tuple[ProviderCatalogEntry, ...]:
    return tuple(
        entry
        for entry in _PROVIDER_CATALOG.values()
        if entry.support_tier in {"core", "evidence"}
    )


def get_provider_catalog_entry(provider_id: str) -> ProviderCatalogEntry | None:
    normalized = str(provider_id or "").strip().lower()
    direct = _PROVIDER_CATALOG.get(normalized)
    if direct is not None:
        return direct
    return next(
        (entry for entry in _PROVIDER_CATALOG.values() if normalized in entry.aliases),
        None,
    )


def get_provider_catalog_entry_for_adapter(
    adapter_id: str,
) -> ProviderCatalogEntry | None:
    normalized = str(adapter_id or "").strip().lower()
    return _CONFIG_ADAPTER_CATALOG.get(normalized)


__all__ = [
    "ANTHROPIC_OAUTH",
    "GOOGLE_ANTIGRAVITY_OAUTH",
    "GOOGLE_GEMINI_CLI_OAUTH",
    "OPENAI_CODEX_OAUTH",
    "OAuthFlowSpec",
    "ProviderCatalogEntry",
    "get_provider_catalog_entry",
    "get_provider_catalog_entry_for_adapter",
    "product_provider_catalog",
    "provider_catalog",
    "routable_provider_catalog",
]
