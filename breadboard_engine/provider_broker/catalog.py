"""Data-driven provider and authentication-flow catalog.

The catalog owns provider metadata; flow adapters consume the exact endpoint
values recorded in the source-established provider specifications. Adding an
OpenAI-compatible provider is therefore a data entry, not a new runtime class.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any


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
    auth_schemes: tuple[str, ...] = ("api_key",)
    oauth_flows: tuple[OAuthFlowSpec, ...] = ()
    compatible_protocol: str = "openai"
    base_url: str | None = None

    def as_view(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "display_name": self.display_name,
            "auth_schemes": list(self.auth_schemes),
            "login_available": any(flow.resolved_client_id() is not None for flow in self.oauth_flows),
            "oauth_flows": [flow.flow_id for flow in self.oauth_flows],
            "runtime_id": self.runtime_id,
            "compatible_protocol": self.compatible_protocol,
            "base_url": self.base_url,
        }


OPENAI_CODEX_OAUTH = OAuthFlowSpec(
    flow_id="openai-codex",
    auth_url="https://auth.openai.com/oauth/authorize",
    token_url="https://auth.openai.com/oauth/token",
    client_id="app_EMoamEEZ73f0CkXaXp7hrann",
    scopes=("openid", "profile", "email", "offline_access", "api.connectors.read", "api.connectors.invoke"),
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


_PROVIDER_CATALOG: dict[str, ProviderCatalogEntry] = {
    "codex": ProviderCatalogEntry("codex", "Codex", "codex_app_server", ("api_key", "oauth2"), (OPENAI_CODEX_OAUTH,)),
    "openai": ProviderCatalogEntry("openai", "OpenAI", "openai_chat"),
    "openrouter": ProviderCatalogEntry("openrouter", "OpenRouter", "openrouter_chat", base_url="https://openrouter.ai/api/v1"),
    "anthropic": ProviderCatalogEntry("anthropic", "Anthropic", "anthropic_messages", ("api_key", "oauth2"), (ANTHROPIC_OAUTH,), compatible_protocol="anthropic"),
    "google-gemini-cli": ProviderCatalogEntry("google-gemini-cli", "Google Cloud Code Assist (Gemini CLI)", "openai_chat", ("oauth2",), (GOOGLE_GEMINI_CLI_OAUTH,)),
    "google-antigravity": ProviderCatalogEntry("google-antigravity", "Antigravity", "openai_chat", ("oauth2",), (GOOGLE_ANTIGRAVITY_OAUTH,)),
    "mock": ProviderCatalogEntry("mock", "Mock", "mock_chat"),
    "cli_mock": ProviderCatalogEntry("cli_mock", "CLI Mock", "cli_mock_chat"),
}


def provider_catalog() -> tuple[ProviderCatalogEntry, ...]:
    return tuple(_PROVIDER_CATALOG.values())


def get_provider_catalog_entry(provider_id: str) -> ProviderCatalogEntry | None:
    return _PROVIDER_CATALOG.get(str(provider_id or "").strip().lower())


def register_provider_catalog_entry(entry: ProviderCatalogEntry) -> None:
    """Register data only; adapters are selected from the entry's flow specs."""
    if not entry.provider_id.strip():
        raise ValueError("provider_id is required")
    _PROVIDER_CATALOG[entry.provider_id.strip().lower()] = entry


__all__ = [
    "ANTHROPIC_OAUTH",
    "GOOGLE_ANTIGRAVITY_OAUTH",
    "GOOGLE_GEMINI_CLI_OAUTH",
    "OPENAI_CODEX_OAUTH",
    "OAuthFlowSpec",
    "ProviderCatalogEntry",
    "get_provider_catalog_entry",
    "provider_catalog",
    "register_provider_catalog_entry",
]
