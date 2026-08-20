"""In-process provider credential broker.

The public broker surface deliberately exchanges JSON-like values only.  Secret
material is held by the SQLite store and is issued narrowly to provider
routing; credential listings and audit records contain metadata only.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from breadboard_engine.security import redaction

from .store import SQLiteCredentialStore


@dataclass(frozen=True)
class BrokerProblem:
    code: str
    message: str
    details: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": dict(self.details or {}),
        }


class ProviderBroker:
    """Single-process broker with an out-of-process-compatible data boundary."""

    def __init__(
        self,
        store: SQLiteCredentialStore | None = None,
        *,
        audit_sink: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.store = store or SQLiteCredentialStore()
        self._audit_sink = audit_sink
        self._audit: list[dict[str, Any]] = []
        self._lock = threading.RLock()

    @staticmethod
    def _value(input_data: Any, *names: str, default: Any = None) -> Any:
        if input_data is None:
            return default
        if isinstance(input_data, Mapping):
            for name in names:
                if name in input_data:
                    return input_data[name]
            return default
        for name in names:
            try:
                value = getattr(input_data, name)
            except AttributeError:
                continue
            return value
        return default

    @staticmethod
    def _problem(code: str, message: str, **details: Any) -> dict[str, Any]:
        return {"code": code, "message": message, "details": details}

    def _emit(self, event: str, **fields: Any) -> None:
        payload, _problems = redaction.scrub_structure(
            {"event": event, "timestamp_ms": __import__("time").time_ns() // 1_000_000, **fields}
        )
        if not isinstance(payload, dict):
            return
        with self._lock:
            self._audit.append(dict(payload))
        if self._audit_sink is not None:
            try:
                self._audit_sink(dict(payload))
            except Exception:
                pass

    def audit_events(self) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(item) for item in self._audit]

    def listProviders(self) -> list[dict[str, Any]]:
        """List catalog providers without importing runtime implementations."""
        providers = (
            ("openai", "OpenAI", "api_key"),
            ("anthropic", "Anthropic", "api_key"),
            ("openrouter", "OpenRouter", "api_key"),
            ("codex", "Codex", "api_key"),
            ("mock", "Mock", "api_key"),
            ("cli_mock", "CLI Mock", "api_key"),
        )
        return [
            {
                "provider_id": provider_id,
                "display_name": display_name,
                "auth_schemes": [auth_scheme],
                "login_available": False,
            }
            for provider_id, display_name, auth_scheme in providers
        ]

    def listCredentials(self, providerId: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        provider_id = providerId or kwargs.get("provider_id")
        return [dict(item) for item in self.store.inspect_accounts(provider_id)]

    def beginLogin(self, input: Any = None, **kwargs: Any) -> dict[str, Any]:
        payload = input if input is not None else kwargs
        provider_id = str(self._value(payload, "providerId", "provider_id", default="")).strip().lower()
        if not provider_id:
            return {"status": "failed", "problem": self._problem("invalid_request", "provider_id is required")}
        problem = self._problem(
            "flow_unavailable",
            f"No established login flow is available for provider '{provider_id}'.",
            provider_id=provider_id,
        )
        login = self.store.create_login(provider_id, "unavailable", problem)
        self._emit("provider_login_unavailable", provider_id=provider_id, login_session_id=login["login_session_id"])
        return login

    def getLogin(self, loginSessionId: str | None = None, **kwargs: Any) -> dict[str, Any]:
        login_id = loginSessionId or kwargs.get("login_session_id") or kwargs.get("loginSessionId")
        if not login_id:
            return {"status": "failed", "problem": self._problem("invalid_request", "login_session_id is required")}
        login = self.store.get_login(str(login_id))
        if login is None:
            return {
                "login_session_id": str(login_id),
                "status": "failed",
                "problem": self._problem("not_found", "login session not found"),
            }
        return dict(login)

    def completeLogin(self, input: Any = None, **kwargs: Any) -> dict[str, Any]:
        payload = input if input is not None else kwargs
        login_id = self._value(payload, "loginSessionId", "login_session_id")
        if not login_id:
            return {"status": "failed", "problem": self._problem("invalid_request", "login_session_id is required")}
        login = self.store.get_login(str(login_id))
        if login is None:
            return {
                "login_session_id": str(login_id),
                "status": "failed",
                "problem": self._problem("not_found", "login session not found"),
            }
        problem = self._problem(
            "flow_unavailable",
            "The requested provider login flow is not established.",
            provider_id=login["provider_id"],
        )
        result = dict(login)
        result.update({"status": "unavailable", "problem": problem})
        self._emit("provider_login_unavailable", provider_id=login["provider_id"], login_session_id=str(login_id))
        return result

    def cancelLogin(self, loginSessionId: str | None = None, **kwargs: Any) -> dict[str, Any]:
        login_id = loginSessionId or kwargs.get("login_session_id") or kwargs.get("loginSessionId")
        if not login_id:
            return {"ok": False, "problem": self._problem("invalid_request", "login_session_id is required")}
        changed = self.store.cancel_login(str(login_id))
        self._emit("provider_login_cancelled", login_session_id=str(login_id), changed=changed)
        return {"ok": bool(changed), "login_session_id": str(login_id)}

    def putApiKey(self, input: Any = None, **kwargs: Any) -> dict[str, Any]:
        payload = input if input is not None else kwargs
        provider_id = str(self._value(payload, "providerId", "provider_id", default="")).strip().lower()
        api_key = self._value(payload, "apiKey", "api_key", "secret")
        if not provider_id or not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("provider_id and api_key are required")
        headers = self._value(payload, "headers", default={})
        headers = dict(headers) if isinstance(headers, Mapping) else {}
        base_url = self._value(payload, "baseUrl", "base_url")
        routing = self._value(payload, "routing", default={})
        metadata = self._value(payload, "metadata", default={})
        auth_scheme = str(self._value(payload, "authSchemeId", "auth_scheme_id", default="api_key"))
        label = str(self._value(payload, "accountLabel", "account_label", "label", default=provider_id))
        alias = str(self._value(payload, "alias", default=""))
        expires_at_ms = self._value(payload, "expiresAtMs", "expires_at_ms")
        ttl_seconds = self._value(payload, "ttlSeconds", "ttl_seconds")
        if expires_at_ms is None and ttl_seconds is not None:
            try:
                import time

                expires_at_ms = int(time.time() * 1000) + max(0, int(ttl_seconds)) * 1000
            except (TypeError, ValueError):
                expires_at_ms = None
        account_id = self._value(payload, "accountId", "account_id")
        material = {"api_key": api_key.strip(), "headers": headers}
        if base_url:
            material["base_url"] = str(base_url)
        if isinstance(routing, Mapping) and routing:
            material["routing"] = dict(routing)
        redaction.register_secret_value(api_key)
        for value in headers.values():
            redaction.register_secret_value(value)
        view = self.store.put_api_key(
            provider_id=provider_id,
            auth_scheme_id=auth_scheme,
            label=label,
            alias=alias,
            account_id=str(account_id) if account_id else None,
            expires_at_ms=int(expires_at_ms) if expires_at_ms is not None else None,
            metadata=metadata if isinstance(metadata, Mapping) else {},
            material=material,
        )
        self._emit(
            "credential_stored",
            provider_id=provider_id,
            account_id=view["account_id"],
            credential_id=view["credential_id"],
            secret_version=view["secret_version"],
        )
        return dict(view)

    def logout(self, input: Any = None, **kwargs: Any) -> dict[str, Any]:
        payload = input if input is not None else kwargs
        provider_id = self._value(payload, "providerId", "provider_id")
        account_id = self._value(payload, "accountId", "account_id")
        credential_id = self._value(payload, "credentialId", "credential_id")
        label = self._value(payload, "accountLabel", "account_label", "label")
        count = self.store.disable_accounts(
            provider_id=str(provider_id).lower() if provider_id else None,
            account_id=str(account_id) if account_id else None,
            credential_id=str(credential_id) if credential_id else None,
            label=str(label) if label else None,
        )
        self._emit("credential_logout", provider_id=provider_id, account_id=account_id, count=count)
        return {"ok": count > 0, "disabled": count}

    def revoke(self, input: Any = None, **kwargs: Any) -> dict[str, Any]:
        payload = input if input is not None else kwargs
        provider_id = self._value(payload, "providerId", "provider_id")
        account_id = self._value(payload, "accountId", "account_id")
        credential_id = self._value(payload, "credentialId", "credential_id")
        label = self._value(payload, "accountLabel", "account_label", "label")
        count = self.store.revoke_accounts(
            provider_id=str(provider_id).lower() if provider_id else None,
            account_id=str(account_id) if account_id else None,
            credential_id=str(credential_id) if credential_id else None,
            label=str(label) if label else None,
        )
        self._emit("credential_revoked", provider_id=provider_id, account_id=account_id, count=count)
        return {"ok": count > 0, "revoked": count}

    def issue_execution_material(
        self,
        provider_id: str,
        *,
        session_id: str = "",
        endpoint_id: str = "",
        account_selector: Any = None,
        minimum_validity_ms: int = 0,
    ) -> dict[str, Any] | None:
        account_id = self._value(account_selector, "accountId", "account_id")
        credential_id = self._value(account_selector, "credentialId", "credential_id")
        label = self._value(account_selector, "accountLabel", "account_label", "label")
        material = self.store.acquire_lease(
            provider_id=provider_id,
            session_id=session_id,
            endpoint_id=endpoint_id,
            account_id=str(account_id) if account_id else None,
            credential_id=str(credential_id) if credential_id else None,
            label=str(label) if label else None,
            minimum_validity_ms=minimum_validity_ms,
        )
        if material is not None:
            redaction.register_secret_value(material.get("api_key"))
            for value in dict(material.get("headers") or {}).values():
                redaction.register_secret_value(value)
        return dict(material) if material is not None else None

    def release_execution_material(self, lease_id: str) -> bool:
        return self.store.release_lease(lease_id)

    # Python spellings are useful to the FastAPI layer while the public wire
    # contract remains the nine camelCase methods above.
    list_providers = listProviders
    list_credentials = listCredentials
    begin_login = beginLogin
    get_login = getLogin
    complete_login = completeLogin
    cancel_login = cancelLogin
    put_api_key = putApiKey


_default_lock = threading.Lock()
_default_broker: ProviderBroker | None = None


def get_provider_broker() -> ProviderBroker:
    """Return the one in-process broker for this engine process."""
    global _default_broker
    with _default_lock:
        if _default_broker is None:
            _default_broker = ProviderBroker()
        return _default_broker


provider_broker = get_provider_broker
