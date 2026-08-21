"""In-process provider credential broker.

The public broker surface deliberately exchanges JSON-like values only.  Secret
material is held by the SQLite store and is issued narrowly to provider
routing; credential listings and audit records contain metadata only.
"""

import time

import threading
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from breadboard_engine.security import redaction

from .store import SQLiteCredentialStore
from .catalog import get_provider_catalog_entry, provider_catalog
from .oauth import OAuthFlowAdapter, OAuthFlowError, OAuthTransport


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
        oauth_transport: OAuthTransport | None = None,
    ) -> None:
        self.store = store or SQLiteCredentialStore()
        self._audit_sink = audit_sink
        self._oauth_transport = oauth_transport
        self._audit: list[dict[str, Any]] = []
        self._lock = threading.RLock()
        self._refresh_locks: dict[str, threading.Lock] = {}

    @staticmethod
    def _value(input_data: Any, *names: str, default: Any = None) -> Any:
        if input_data is None:
            return default
        if isinstance(input_data, Mapping):
            for name in names:
                if name not in input_data:
                    continue
                value = input_data[name]
                if value is not None and (not isinstance(value, str) or value.strip()):
                    return value
            return default
        for name in names:
            try:
                value = getattr(input_data, name)
            except AttributeError:
                continue
            if value is not None and (not isinstance(value, str) or value.strip()):
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
        """List data-driven provider catalog entries without runtime imports."""
        return [entry.as_view() for entry in provider_catalog()]

    def listCredentials(self, providerId: str | None = None, **kwargs: Any) -> list[dict[str, Any]]:
        provider_id = providerId or kwargs.get("provider_id")
        return [dict(item) for item in self.store.inspect_accounts(provider_id)]

    def _oauth_adapter(self, provider_id: str, flow_id: str | None = None) -> OAuthFlowAdapter | None:
        entry = get_provider_catalog_entry(provider_id)
        if entry is None or not entry.oauth_flows:
            return None
        spec = next((flow for flow in entry.oauth_flows if flow.flow_id == flow_id), entry.oauth_flows[0])
        return OAuthFlowAdapter(spec, transport=self._oauth_transport)

    @staticmethod
    def _public_login(login: Mapping[str, Any]) -> dict[str, Any]:
        result = {key: value for key, value in login.items() if key != "flow"}
        flow = login.get("flow")
        if isinstance(flow, Mapping):
            for key in ("flow_id", "flow_kind", "authorization_url", "redirect_uri", "user_code", "instructions"):
                if key in flow:
                    result[key] = flow[key]
        return result

    def beginLogin(self, input: Any = None, **kwargs: Any) -> dict[str, Any]:
        payload = input if input is not None else kwargs
        provider_id = str(self._value(payload, "providerId", "provider_id", default="")).strip().lower()
        if not provider_id:
            return {"status": "failed", "problem": self._problem("invalid_request", "provider_id is required")}
        flow_id = self._value(payload, "flowId", "flow_id")
        flow_kind = str(self._value(payload, "flow", "flow_kind", default="browser")).strip().lower()
        adapter = self._oauth_adapter(provider_id, str(flow_id) if flow_id else None)
        if adapter is None:
            problem = self._problem("flow_unavailable", f"No established login flow is available for provider '{provider_id}'.", provider_id=provider_id)
            login = self.store.create_login(provider_id, "unavailable", problem)
            self._emit("provider_login_unavailable", provider_id=provider_id, login_session_id=login["login_session_id"])
            return login
        try:
            started = adapter.begin(flow_kind=flow_kind)
        except OAuthFlowError as exc:
            problem = self._problem(exc.code, str(exc), provider_id=provider_id, **exc.details)
            login = self.store.create_login(provider_id, "unavailable", problem)
            self._emit("provider_login_unavailable", provider_id=provider_id, login_session_id=login["login_session_id"])
            return login
        flow = dict(started.internal)
        flow.update(started.public)
        login = self.store.create_login(provider_id, "pending", flow=flow)
        self._emit("provider_login_started", provider_id=provider_id, login_session_id=login["login_session_id"], flow_id=flow.get("flow_id"), flow_kind=flow.get("flow_kind"))
        login_with_flow = dict(login)
        login_with_flow["flow"] = flow
        return self._public_login(login_with_flow)

    def getLogin(self, loginSessionId: str | None = None, **kwargs: Any) -> dict[str, Any]:
        login_id = loginSessionId or kwargs.get("login_session_id") or kwargs.get("loginSessionId")
        if not login_id:
            return {"status": "failed", "problem": self._problem("invalid_request", "login_session_id is required")}
        login = self.store.get_login(str(login_id))
        if login is None:
            return {"login_session_id": str(login_id), "status": "failed", "problem": self._problem("not_found", "login session not found")}
        return self._public_login(login)

    def completeLogin(self, input: Any = None, **kwargs: Any) -> dict[str, Any]:
        payload = input if input is not None else kwargs
        login_id = self._value(payload, "loginSessionId", "login_session_id")
        if not login_id:
            return {"status": "failed", "problem": self._problem("invalid_request", "login_session_id is required")}
        login = self.store.get_login(str(login_id), include_flow=True)
        if login is None:
            return {"login_session_id": str(login_id), "status": "failed", "problem": self._problem("not_found", "login session not found")}
        flow = login.get("flow") if isinstance(login.get("flow"), Mapping) else {}
        adapter = self._oauth_adapter(str(login["provider_id"]), str(flow.get("flow_id")) if flow.get("flow_id") else None)
        if adapter is None:
            problem = self._problem("flow_unavailable", "The requested provider login flow is not established.", provider_id=login["provider_id"])
            return {**self._public_login(login), "status": "unavailable", "problem": problem}
        try:
            material = adapter.complete(
                flow,
                code=self._value(payload, "code", "authorizationCode", "authorization_code"),
                state=self._value(payload, "state"),
            )
            provider_id = str(login["provider_id"])
            entry = get_provider_catalog_entry(provider_id)
            store_provider = entry.oauth_flows[0].store_provider_id if entry and entry.oauth_flows else None
            store_provider = store_provider or provider_id
            label = str(self._value(payload, "accountLabel", "account_label", "label", default=material.get("email") or store_provider))
            metadata = {key: material[key] for key in ("email", "provider_account_id", "project_id", "token_type") if key in material}
            view = self.store.put_oauth(
                provider_id=store_provider,
                auth_scheme_id="oauth2",
                label=label,
                alias=str(self._value(payload, "alias", default="")),
                expires_at_ms=int(material["expires_at_ms"]),
                material=material,
                metadata=metadata,
            )
            for value in (material.get("access_token"), material.get("refresh_token")):
                redaction.register_secret_value(value)
            self.store.finish_login(str(login_id), "completed")
            self._emit("provider_login_completed", provider_id=provider_id, account_id=view["account_id"], credential_id=view["credential_id"])
            return {**self._public_login(self.store.get_login(str(login_id)) or login), "status": "completed", "credential": view}
        except OAuthFlowError as exc:
            problem = self._problem(exc.code, str(exc), provider_id=login["provider_id"], **exc.details)
            self.store.finish_login(str(login_id), "failed", problem)
            self._emit("provider_login_failed", provider_id=login["provider_id"], login_session_id=str(login_id), code=exc.code)
            return {**self._public_login(self.store.get_login(str(login_id)) or login), "status": "failed", "problem": problem}

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
            minimum_validity_ms=0,
        )
        if material is None:
            return None
        expires_at = material.get("expires_at_ms")
        refresh_token = material.get("refresh_token")
        now = int(time.time() * 1000)
        if isinstance(expires_at, (int, float)) and expires_at <= now + max(0, int(minimum_validity_ms)) and isinstance(refresh_token, str):
            account_ref = str(material.get("account_id") or "")
            old_version = int(material.get("secret_version") or 0)
            with self._lock:
                refresh_lock = self._refresh_locks.setdefault(account_ref, threading.Lock())
            with refresh_lock:
                self.store.release_lease(str(material.get("lease_id") or ""))
                latest = self.store.acquire_lease(
                    provider_id=provider_id,
                    session_id=session_id,
                    endpoint_id=endpoint_id,
                    account_id=account_ref,
                    minimum_validity_ms=0,
                )
                latest_expires = latest.get("expires_at_ms") if latest else None
                if latest is not None and int(latest.get("secret_version") or 0) > old_version and (
                    not isinstance(latest_expires, (int, float))
                    or latest_expires > int(time.time() * 1000) + max(0, int(minimum_validity_ms))
                ):
                    material = latest
                else:
                    if latest is not None:
                        self.store.release_lease(str(latest.get("lease_id") or ""))
                    try:
                        adapter = self._oauth_adapter(str(material.get("provider_id") or provider_id))
                        if adapter is not None:
                            refreshed = adapter.refresh(material)
                            refreshed_metadata = {key: material[key] for key in ("email", "provider_account_id", "project_id") if key in material}
                            refreshed_view = self.store.put_oauth(
                                provider_id=str(material.get("provider_id") or provider_id),
                                auth_scheme_id=str(material.get("auth_scheme_id") or "oauth2"),
                                label=str(material.get("label") or provider_id),
                                account_id=account_ref,
                                expires_at_ms=int(refreshed["expires_at_ms"]),
                                material=refreshed,
                                metadata=refreshed_metadata,
                            )
                            redaction.register_secret_value(refreshed.get("access_token"))
                            redaction.register_secret_value(refreshed.get("refresh_token"))
                            self._emit("provider_credential_refreshed", provider_id=provider_id, account_id=refreshed_view["account_id"], secret_version=refreshed_view["secret_version"])
                            material = self.store.acquire_lease(
                                provider_id=provider_id,
                                session_id=session_id,
                                endpoint_id=endpoint_id,
                                account_id=account_ref,
                                minimum_validity_ms=minimum_validity_ms,
                            )
                    except OAuthFlowError as exc:
                        self._emit("provider_credential_refresh_failed", provider_id=provider_id, account_id=account_ref, code=exc.code)
                        material = self.store.acquire_lease(
                            provider_id=provider_id,
                            session_id=session_id,
                            endpoint_id=endpoint_id,
                            account_id=account_ref,
                            minimum_validity_ms=0,
                        )
        if material is not None:
            redaction.register_secret_value(material.get("api_key"))
            redaction.register_secret_value(material.get("access_token"))
            redaction.register_secret_value(material.get("refresh_token"))
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
