"""In-process provider credential broker.

The public broker surface deliberately exchanges JSON-like values only.  Secret
material is held by the SQLite store and is issued narrowly to provider
routing; credential listings and audit records contain metadata only.
"""

import json
import os
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from breadboard_engine import security
from breadboard_engine.security import redaction

from .catalog import get_provider_catalog_entry, product_provider_catalog
from .oauth import (
    DEFAULT_OAUTH_HTTP_TIMEOUT_SECONDS,
    OAuthFlowAdapter,
    OAuthFlowError,
    OAuthTransport,
)
from .store import SQLiteCredentialStore


# Highest precedence first. Raw runtime/config values remain broker-owned; agent
# configuration never projects them into process or Ray environments.
AUTH_SOURCE_PRECEDENCE = (
    "runtime",
    "config",
    "oauth",
    "login_api_key",
    "env",
    "stored_api_key",
    "fallback",
)
REMOTE_BROKER_URL_ENV = "BREADBOARD_AUTH_BROKER_URL"
REMOTE_BROKER_CONFIGURED_ENV = "BREADBOARD_AUTH_BROKER_CONFIGURED"


class ProviderBrokerConfigurationError(RuntimeError):
    """Raised when an explicit broker configuration cannot be honored safely."""


class CredentialAuditPersistenceError(RuntimeError):
    """A required durable audit append failed without exposing its cause."""

    def __init__(self) -> None:
        super().__init__()


@dataclass(frozen=True)
class CredentialOrigin:
    """Secret-free provenance for the credential selected by one operation."""

    kind: str
    account_id: str | None = None
    credential_id: str | None = None
    env_var: str | None = None
    source: str | None = None
    binding_kind: str | None = None
    binding_reason: str | None = None

    def to_dict(self) -> dict[str, str]:
        return {
            key: value
            for key, value in (
                ("kind", self.kind),
                ("account_id", self.account_id),
                ("credential_id", self.credential_id),
                ("env_var", self.env_var),
                ("source", self.source),
                ("binding_kind", self.binding_kind),
                ("binding_reason", self.binding_reason),
            )
            if value
        }


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
        fallback_resolver: (Callable[[str], Mapping[str, Any] | None] | None) = None,
        fallback_origins: Mapping[str, str] | None = None,
        codex_auth_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self.store = store or SQLiteCredentialStore()
        self._audit_sink = audit_sink
        self._oauth_transport = oauth_transport
        self._fallback_resolver = fallback_resolver
        self._fallback_origins = {
            str(provider_id).strip().lower(): str(source).strip()
            for provider_id, source in dict(fallback_origins or {}).items()
            if str(provider_id).strip() and str(source).strip()
        }
        self._codex_auth_path = (
            Path(codex_auth_path).expanduser().resolve()
            if codex_auth_path is not None
            else (Path.home() / ".codex" / "auth.json").resolve()
        )
        register = getattr(security, "register_protected_credential_path", None)
        if register is not None:
            register(str(self._codex_auth_path), sqlite_sidecars=False)
        self._runtime_overrides: dict[str, dict[str, Any]] = {}
        self._config_overrides: dict[str, dict[str, Any]] = {}
        self._audit: list[dict[str, Any]] = []
        self._lock = threading.RLock()

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
    def _clear_mutable_material(value: Any) -> None:
        if isinstance(value, bytearray):
            value[:] = b"\0" * len(value)
            value.clear()
            return
        if isinstance(value, dict):
            for item in tuple(value.values()):
                ProviderBroker._clear_mutable_material(item)
            value.clear()
            return
        if isinstance(value, list):
            for item in tuple(value):
                ProviderBroker._clear_mutable_material(item)
            value.clear()

    @staticmethod
    def _find_token(value: Any, seen: set[int] | None = None) -> str | None:
        visited = set() if seen is None else seen
        if value is None:
            return None
        marker = id(value)
        if marker in visited:
            return None
        visited.add(marker)
        if isinstance(value, Mapping):
            for key in (
                "codex_access_token",
                "access_token",
                "id_token",
                "token",
                "auth_token",
            ):
                candidate = value.get(key)
                if isinstance(candidate, str) and candidate.strip():
                    return candidate.strip()
            for nested in value.values():
                found = ProviderBroker._find_token(nested, visited)
                if found:
                    return found
        elif isinstance(value, list):
            for nested in value:
                found = ProviderBroker._find_token(nested, visited)
                if found:
                    return found
        return None

    def _default_fallback_material(
        self,
        provider_id: str,
    ) -> dict[str, Any] | None:
        if provider_id != "codex":
            return None
        try:
            payload = json.loads(self._codex_auth_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(payload, Mapping):
            return None
        api_key = payload.get("OPENAI_API_KEY")
        if isinstance(api_key, str) and api_key.strip():
            return {
                "api_key": api_key.strip(),
                "_origin_source": "codex_auth_file",
            }
        token = self._find_token(payload)
        if token:
            return {
                "api_key": token,
                "headers": {"Authorization": f"Bearer {token}"},
                "_origin_source": "codex_auth_file",
            }
        return None

    def _load_fallback_material(
        self,
        provider_id: str,
    ) -> dict[str, Any] | None:
        try:
            resolved = (
                self._fallback_resolver(provider_id)
                if self._fallback_resolver is not None
                else self._default_fallback_material(provider_id)
            )
        except Exception:
            return None
        return self._copy_material(resolved) if isinstance(resolved, Mapping) else None

    def _fallback_origin_source(self, provider_id: str) -> str | None:
        provider = str(provider_id).strip().lower()
        with self._lock:
            configured = self._fallback_origins.get(provider)
        if configured:
            return configured
        if (
            provider == "codex"
            and self._fallback_resolver is None
            and self._codex_auth_path.is_file()
        ):
            return "codex_auth_file"
        return None

    @staticmethod
    def _override_material(
        api_key: str,
        *,
        base_url: str | None = None,
        headers: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        key = str(api_key).strip()
        if not key:
            raise ValueError("api_key is required")
        material: dict[str, Any] = {"api_key": key}
        if base_url:
            material["base_url"] = str(base_url)
        if headers:
            material["headers"] = {
                str(name): str(value)
                for name, value in headers.items()
                if name and value is not None
            }
        return material

    def _set_override(
        self,
        target: dict[str, dict[str, Any]],
        provider_id: str,
        material: dict[str, Any],
    ) -> None:
        normalized = str(provider_id).strip().lower()
        if not normalized:
            self._clear_mutable_material(material)
            raise ValueError("provider_id is required")
        with self._lock:
            previous = target.pop(normalized, None)
            target[normalized] = material
        if previous is not None:
            self._clear_mutable_material(previous)

    def set_runtime_api_key(
        self,
        provider_id: str,
        api_key: str,
        *,
        base_url: str | None = None,
        headers: Mapping[str, Any] | None = None,
    ) -> None:
        """Install a process-lifetime override inside the broker boundary."""
        self._set_override(
            self._runtime_overrides,
            provider_id,
            self._override_material(
                api_key,
                base_url=base_url,
                headers=headers,
            ),
        )

    def remove_runtime_api_key(self, provider_id: str) -> None:
        with self._lock:
            material = self._runtime_overrides.pop(
                str(provider_id).strip().lower(),
                None,
            )
        if material is not None:
            self._clear_mutable_material(material)

    def set_config_api_key(
        self,
        provider_id: str,
        api_key: str,
        *,
        base_url: str | None = None,
        headers: Mapping[str, Any] | None = None,
    ) -> None:
        """Install a selected-config override inside the broker boundary."""
        self._set_override(
            self._config_overrides,
            provider_id,
            self._override_material(
                api_key,
                base_url=base_url,
                headers=headers,
            ),
        )

    def remove_config_api_key(self, provider_id: str) -> None:
        with self._lock:
            material = self._config_overrides.pop(
                str(provider_id).strip().lower(),
                None,
            )
        if material is not None:
            self._clear_mutable_material(material)

    def clear_config_api_keys(self) -> None:
        with self._lock:
            materials = tuple(self._config_overrides.values())
            self._config_overrides.clear()
        for material in materials:
            self._clear_mutable_material(material)

    @staticmethod
    def _problem(code: str, message: str, **details: Any) -> dict[str, Any]:
        return {"code": code, "message": message, "details": details}

    def _emit(self, event: str, **fields: Any) -> None:
        occurred_at_ms = time.time_ns() // 1_000_000
        payload, _problems = redaction.scrub_structure(
            {
                "event_id": f"bbaudit_{uuid.uuid4().hex}",
                "event": event,
                "occurred_at_ms": occurred_at_ms,
                "timestamp_ms": occurred_at_ms,
                "actor": "local_process",
                "origin": "provider_broker",
                "outcome": fields.pop("outcome", "success"),
                **fields,
            }
        )
        if not isinstance(payload, dict):
            return
        try:
            persisted = self.store.append_audit_event(payload)
        except Exception:
            raise CredentialAuditPersistenceError() from None
        published = dict(persisted)

        def publish() -> None:
            with self._lock:
                self._audit.append(dict(published))
            if self._audit_sink is not None:
                try:
                    self._audit_sink(dict(published))
                except Exception:
                    pass

        self.store.after_commit(publish)

    def audit_events(self) -> list[dict[str, Any]]:
        return self.store.list_audit_events()

    def listProviders(self) -> list[dict[str, Any]]:
        """List only the approved product tier with secret-free availability."""
        result: list[dict[str, Any]] = []
        for entry in product_provider_catalog():
            if entry.auth_owner == "provider":
                available = True
                reason = "provider_managed"
            else:
                origin = self.get_credential_origin(
                    entry.provider_id,
                    environment_key=entry.api_key_env,
                )
                available = origin is not None
                reason = None if available else "missing_auth"
            result.append(
                entry.as_view(
                    available=available,
                    availability_reason=reason,
                )
            )
        return result

    def listCredentials(
        self, providerId: str | None = None, **kwargs: Any
    ) -> list[dict[str, Any]]:
        provider_id = providerId or kwargs.get("provider_id")
        return [dict(item) for item in self.store.inspect_accounts(provider_id)]

    def _oauth_adapter(
        self, provider_id: str, flow_id: str | None = None
    ) -> OAuthFlowAdapter | None:
        entry = get_provider_catalog_entry(provider_id)
        if entry is None or not entry.oauth_flows:
            return None
        spec = next(
            (flow for flow in entry.oauth_flows if flow.flow_id == flow_id),
            entry.oauth_flows[0],
        )
        return OAuthFlowAdapter(spec, transport=self._oauth_transport)

    @staticmethod
    def _public_login(login: Mapping[str, Any]) -> dict[str, Any]:
        allowed = {
            "login_session_id",
            "provider_id",
            "status",
            "created_at_ms",
            "updated_at_ms",
            "expires_at_ms",
            "problem",
            "credential",
        }
        result = {key: login[key] for key in allowed if key in login}
        flow = login.get("flow")
        if isinstance(flow, Mapping):
            for key in ("flow_id", "flow_kind", "authorization_url", "redirect_uri"):
                if key in flow:
                    result[key] = flow[key]
        scrubbed, _problems = redaction.scrub_structure(result, path="$.login")
        return scrubbed if isinstance(scrubbed, dict) else {}

    def beginLogin(self, input: Any = None, **kwargs: Any) -> dict[str, Any]:
        payload = input if input is not None else kwargs
        provider_id = (
            str(self._value(payload, "providerId", "provider_id", default=""))
            .strip()
            .lower()
        )
        if not provider_id:
            return {
                "status": "failed",
                "problem": self._problem("invalid_request", "provider_id is required"),
            }
        flow_id = self._value(payload, "flowId", "flow_id")
        flow_kind = (
            str(self._value(payload, "flow", "flow_kind", default="browser"))
            .strip()
            .lower()
        )
        adapter = self._oauth_adapter(provider_id, str(flow_id) if flow_id else None)
        if adapter is None:
            problem = self._problem(
                "flow_unavailable",
                f"No established login flow is available for provider '{provider_id}'.",
                provider_id=provider_id,
            )
            with self.store.atomic():
                login = self.store.create_login(provider_id, "unavailable", problem)
                self._emit(
                    "provider_login_unavailable",
                    provider_id=provider_id,
                    login_session_id=login["login_session_id"],
                )
            return login
        try:
            started = adapter.begin(flow_kind=flow_kind)
        except OAuthFlowError as exc:
            problem = self._problem(
                exc.code,
                redaction.safe_exception_message(exc, operation="OAuth flow"),
                provider_id=provider_id,
                **exc.details,
            )
            with self.store.atomic():
                login = self.store.create_login(provider_id, "unavailable", problem)
                self._emit(
                    "provider_login_unavailable",
                    provider_id=provider_id,
                    login_session_id=login["login_session_id"],
                )
            return login
        stored_flow = dict(started.internal)
        with self.store.atomic():
            login = self.store.create_login(
                provider_id,
                "pending",
                flow=stored_flow,
            )
            self._emit(
                "provider_login_started",
                provider_id=provider_id,
                login_session_id=login["login_session_id"],
                flow_id=stored_flow.get("flow_id"),
                flow_kind=stored_flow.get("flow_kind"),
            )
        login_with_flow = dict(login)
        login_with_flow["flow"] = dict(started.public)
        public = self._public_login(login_with_flow)
        for key in ("authorization_url", "redirect_uri", "user_code", "instructions"):
            if key in started.public:
                public[key] = started.public[key]
        return public

    def getLogin(
        self, loginSessionId: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        login_id = (
            loginSessionId
            or kwargs.get("login_session_id")
            or kwargs.get("loginSessionId")
        )
        if not login_id:
            return {
                "status": "failed",
                "problem": self._problem(
                    "invalid_request", "login_session_id is required"
                ),
            }
        login = self.store.get_login(str(login_id))
        if login is None:
            return {
                "login_session_id": str(login_id),
                "status": "failed",
                "problem": self._problem("not_found", "login session not found"),
            }
        return self._public_login(login)

    def completeLogin(self, input: Any = None, **kwargs: Any) -> dict[str, Any]:
        payload = input if input is not None else kwargs
        login_id = self._value(payload, "loginSessionId", "login_session_id")
        if not login_id:
            return {
                "status": "failed",
                "problem": self._problem(
                    "invalid_request", "login_session_id is required"
                ),
            }
        login = self.store.get_login(str(login_id), include_flow=True)
        if login is None:
            return {
                "login_session_id": str(login_id),
                "status": "failed",
                "problem": self._problem("not_found", "login session not found"),
            }
        if str(login.get("status") or "") != "pending":
            return self._public_login(login)
        flow = login.get("flow") if isinstance(login.get("flow"), Mapping) else {}
        adapter = self._oauth_adapter(
            str(login["provider_id"]),
            str(flow.get("flow_id")) if flow.get("flow_id") else None,
        )
        if adapter is None:
            problem = self._problem(
                "flow_unavailable",
                "The requested provider login flow is not established.",
                provider_id=login["provider_id"],
            )
            return {
                **self._public_login(login),
                "status": "unavailable",
                "problem": problem,
            }

        def login_is_terminal() -> bool:
            current = self.store.get_login(str(login_id))
            return current is None or str(current.get("status") or "") != "pending"

        try:
            material = adapter.complete(
                flow,
                code=self._value(
                    payload, "code", "authorizationCode", "authorization_code"
                ),
                state=self._value(payload, "state"),
                is_cancelled=login_is_terminal,
            )
            oauth_secret_values = redaction.credential_secret_values(material)
            try:
                with redaction.secret_value_scope(
                    *oauth_secret_values,
                    allow_short=True,
                ):
                    with self.store.atomic():
                        if not self.store.finish_pending_login(
                            str(login_id),
                            "completed",
                        ):
                            current = self.store.get_login(str(login_id)) or login
                            return self._public_login(current)
                        provider_id = str(login["provider_id"])
                        entry = get_provider_catalog_entry(provider_id)
                        store_provider = (
                            entry.oauth_flows[0].store_provider_id
                            if entry and entry.oauth_flows
                            else None
                        )
                        store_provider = store_provider or provider_id
                        label = str(
                            self._value(
                                payload,
                                "accountLabel",
                                "account_label",
                                "label",
                                default=material.get("email") or store_provider,
                            )
                        )
                        alias = str(self._value(payload, "alias", default=""))
                        expires_at_ms = int(material["expires_at_ms"])
                        identity_values = (
                            provider_id,
                            store_provider,
                            "oauth2",
                            label,
                            alias,
                            str(expires_at_ms),
                        )
                        if any(
                            redaction.contains_registered_secret_text(value)
                            for value in identity_values
                        ):
                            raise OAuthFlowError(
                                "oauth_invalid_response",
                                (
                                    "OAuth credential identity fields cannot "
                                    "contain credential material"
                                ),
                            )
                        metadata = {
                            key: material[key]
                            for key in (
                                "email",
                                "provider_account_id",
                                "project_id",
                                "token_type",
                            )
                            if key in material
                        }
                        if redaction.contains_registered_secret_mapping_key(metadata):
                            raise OAuthFlowError(
                                "oauth_invalid_response",
                                "OAuth metadata keys cannot contain credential material",
                            )
                        scrubbed_metadata, _ = redaction.scrub_structure(
                            metadata,
                            path="$.metadata",
                        )
                        view = self.store.put_oauth(
                            provider_id=store_provider,
                            auth_scheme_id="oauth2",
                            label=label,
                            alias=alias,
                            expires_at_ms=expires_at_ms,
                            material=material,
                            metadata=scrubbed_metadata,
                            source="login",
                        )
                        self._emit(
                            "provider_login_completed",
                            provider_id=provider_id,
                            account_id=view["account_id"],
                            credential_id=view["credential_id"],
                        )
                    result, _ = redaction.scrub_structure(
                        {
                            **self._public_login(
                                self.store.get_login(str(login_id)) or login
                            ),
                            "status": "completed",
                            "credential": view,
                        },
                        path="$.login",
                    )
                    if not isinstance(result, Mapping):
                        raise OAuthFlowError(
                            "oauth_invalid_response",
                            "OAuth credential store returned an invalid view",
                        )
                    return dict(result)
            finally:
                self._clear_mutable_material(material)
        except OAuthFlowError as exc:
            problem = self._problem(
                exc.code,
                redaction.safe_exception_message(exc, operation="OAuth flow"),
                provider_id=login["provider_id"],
                **exc.details,
            )
            with self.store.atomic():
                changed = self.store.finish_pending_login(
                    str(login_id), "failed", problem
                )
                if changed:
                    self._emit(
                        "provider_login_failed",
                        provider_id=login["provider_id"],
                        login_session_id=str(login_id),
                        code=exc.code,
                    )
            current = self.store.get_login(str(login_id)) or login
            if not changed:
                return self._public_login(current)
            return {
                **self._public_login(current),
                "status": "failed",
                "problem": problem,
            }

    def cancelLogin(
        self, loginSessionId: str | None = None, **kwargs: Any
    ) -> dict[str, Any]:
        login_id = (
            loginSessionId
            or kwargs.get("login_session_id")
            or kwargs.get("loginSessionId")
        )
        if not login_id:
            return {
                "ok": False,
                "problem": self._problem(
                    "invalid_request", "login_session_id is required"
                ),
            }
        with self.store.atomic():
            changed = self.store.cancel_login(str(login_id))
            self._emit(
                "provider_login_cancelled",
                login_session_id=str(login_id),
                changed=changed,
            )
        return {"ok": bool(changed), "login_session_id": str(login_id)}

    def putApiKey(self, input: Any = None, **kwargs: Any) -> dict[str, Any]:
        payload = input if input is not None else kwargs
        provider_id = (
            str(self._value(payload, "providerId", "provider_id", default=""))
            .strip()
            .lower()
        )
        api_key = self._value(payload, "apiKey", "api_key", "secret")
        if not provider_id or not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("provider_id and api_key are required")
        headers = self._value(payload, "headers", default={})
        headers = dict(headers) if isinstance(headers, Mapping) else {}
        base_url = self._value(payload, "baseUrl", "base_url")
        routing = self._value(payload, "routing", default={})
        metadata = self._value(payload, "metadata", default={})
        auth_scheme = str(
            self._value(payload, "authSchemeId", "auth_scheme_id", default="api_key")
        )
        label = str(
            self._value(
                payload, "accountLabel", "account_label", "label", default=provider_id
            )
        )
        alias = str(self._value(payload, "alias", default=""))
        expires_at_ms = self._value(payload, "expiresAtMs", "expires_at_ms")
        ttl_seconds = self._value(payload, "ttlSeconds", "ttl_seconds")
        if expires_at_ms is None and ttl_seconds is not None:
            try:
                import time

                expires_at_ms = (
                    int(time.time() * 1000) + max(0, int(ttl_seconds)) * 1000
                )
            except (TypeError, ValueError):
                expires_at_ms = None
        account_id = self._value(payload, "accountId", "account_id")
        secret_value = api_key.strip()
        if len(secret_value) < redaction.MIN_REGISTERED_SECRET_LENGTH:
            raise ValueError(
                "api_key must contain at least four non-whitespace characters"
            )
        metadata_value = dict(metadata) if isinstance(metadata, Mapping) else {}
        material = {"api_key": secret_value, "headers": headers}
        if base_url:
            material["base_url"] = str(base_url)
        if isinstance(routing, Mapping) and routing:
            material["routing"] = dict(routing)
        credential_values = redaction.credential_secret_values(material)
        try:
            with self.store.atomic():
                with redaction.secret_value_scope(
                    *credential_values,
                    allow_short=True,
                ):
                    identity_values = (
                        provider_id,
                        auth_scheme,
                        label,
                        alias,
                        str(account_id) if account_id is not None else "",
                        str(expires_at_ms) if expires_at_ms is not None else "",
                    )
                    if any(
                        redaction.contains_registered_secret_identity(value)
                        for value in identity_values
                    ):
                        raise ValueError(
                            "credential identity fields cannot contain credential material"
                        )
                    if redaction.contains_registered_secret_mapping_key(
                        metadata_value
                    ):
                        raise ValueError(
                            "metadata keys cannot contain credential material"
                        )
                    scrubbed_metadata, _ = redaction.scrub_structure(
                        metadata_value,
                        path="$.metadata",
                        identity_mapping_keys=True,
                    )
                    view = self.store.put_api_key(
                        provider_id=provider_id,
                        auth_scheme_id=auth_scheme,
                        label=label,
                        alias=alias,
                        account_id=str(account_id) if account_id else None,
                        expires_at_ms=(
                            int(expires_at_ms) if expires_at_ms is not None else None
                        ),
                        metadata=scrubbed_metadata,
                        material=material,
                        source="login",
                    )
                self._emit(
                    "credential_stored",
                    provider_id=provider_id,
                    account_id=view["account_id"],
                    credential_id=view["credential_id"],
                    secret_version=view["secret_version"],
                )
            if not isinstance(view, Mapping):
                raise RuntimeError("credential store returned an invalid view")
            return dict(view)
        finally:
            self._clear_mutable_material(material)

    def logout(self, input: Any = None, **kwargs: Any) -> dict[str, Any]:
        payload = input if input is not None else kwargs
        provider_id = self._value(payload, "providerId", "provider_id")
        account_id = self._value(payload, "accountId", "account_id")
        credential_id = self._value(payload, "credentialId", "credential_id")
        label = self._value(payload, "accountLabel", "account_label", "label")
        with self.store.atomic():
            count = self.store.disable_accounts(
                provider_id=str(provider_id).lower() if provider_id else None,
                account_id=str(account_id) if account_id else None,
                credential_id=str(credential_id) if credential_id else None,
                label=str(label) if label else None,
            )
            self._emit(
                "credential_logout",
                provider_id=provider_id,
                account_id=account_id,
                count=count,
                action="disable",
                secret_disposition="retained",
                tombstone=False,
            )
        return {"ok": count > 0, "disabled": count}

    def revoke(self, input: Any = None, **kwargs: Any) -> dict[str, Any]:
        payload = input if input is not None else kwargs
        provider_id = self._value(payload, "providerId", "provider_id")
        account_id = self._value(payload, "accountId", "account_id")
        credential_id = self._value(payload, "credentialId", "credential_id")
        label = self._value(payload, "accountLabel", "account_label", "label")
        with self.store.atomic():
            count = self.store.revoke_accounts(
                provider_id=str(provider_id).lower() if provider_id else None,
                account_id=str(account_id) if account_id else None,
                credential_id=str(credential_id) if credential_id else None,
                label=str(label) if label else None,
            )
            self._emit(
                "credential_revoked",
                provider_id=provider_id,
                account_id=account_id,
                count=count,
                action="revoke",
                secret_disposition="revoked",
                tombstone=True,
            )
        return {"ok": count > 0, "revoked": count}

    @staticmethod
    def _refresh_failure_class(error: OAuthFlowError) -> str:
        explicit = str(error.details.get("failure_class") or "").strip().lower()
        if explicit in {"transient", "definitive"}:
            return explicit
        if error.code == "oauth_refresh_unavailable":
            return "definitive"
        return "transient"

    @staticmethod
    def _refresh_retry_not_before_ms(error: OAuthFlowError) -> int:
        retry_after = error.details.get("retry_after")
        try:
            seconds = float(retry_after) if retry_after is not None else 1.0
        except (TypeError, ValueError):
            seconds = 1.0
        bounded_seconds = min(300.0, max(1.0, seconds))
        return int(time.time() * 1000 + bounded_seconds * 1000)

    def _acquire_current_account_material(
        self,
        *,
        provider_id: str,
        session_id: str,
        endpoint_id: str,
        account_id: str,
        credential_class: str | None,
        minimum_validity_ms: int,
    ) -> dict[str, Any] | None:
        return self.store.acquire_lease(
            provider_id=provider_id,
            session_id=session_id,
            endpoint_id=endpoint_id,
            account_id=account_id,
            credential_class=credential_class,
            minimum_validity_ms=minimum_validity_ms,
            allow_expired=False,
            bind_explicit=False,
        )

    def _record_refresh_failure(
        self,
        error: OAuthFlowError,
        *,
        provider_id: str,
        account_id: str,
        expected_secret_version: int,
        owner_id: str,
    ) -> None:
        failure_class = self._refresh_failure_class(error)
        retry_not_before_ms = (
            self._refresh_retry_not_before_ms(error)
            if failure_class == "transient"
            else None
        )
        with self.store.atomic():
            applied = self.store.fail_oauth_refresh(
                account_id=account_id,
                expected_secret_version=expected_secret_version,
                owner_id=owner_id,
                failure_class=failure_class,
                failure_code=error.code,
                retry_not_before_ms=retry_not_before_ms,
            )
            self._emit(
                "provider_credential_refresh_failed",
                provider_id=provider_id,
                account_id=account_id,
                code=error.code,
                failure_class=failure_class,
                applied=applied,
            )

    def _refresh_with_heartbeat(
        self,
        adapter: OAuthFlowAdapter,
        material: Mapping[str, Any],
        *,
        account_id: str,
        expected_secret_version: int,
        owner_id: str,
        lease_duration_ms: int,
    ) -> dict[str, Any]:
        stop = threading.Event()
        interval_seconds = max(
            0.05,
            min(5.0, lease_duration_ms / 3_000),
        )

        def heartbeat() -> None:
            while not stop.wait(interval_seconds):
                try:
                    renewed = self.store.renew_oauth_refresh(
                        account_id=account_id,
                        expected_secret_version=expected_secret_version,
                        owner_id=owner_id,
                        lease_duration_ms=lease_duration_ms,
                    )
                except Exception:
                    continue
                if not renewed:
                    return

        worker = threading.Thread(
            target=heartbeat,
            name="bb-oauth-refresh-heartbeat",
            daemon=True,
        )
        worker.start()
        try:
            return adapter.refresh(material)
        finally:
            stop.set()
            worker.join(timeout=1)

    def _refresh_stored_material(
        self,
        stale_material: dict[str, Any],
        *,
        provider_id: str,
        session_id: str,
        endpoint_id: str,
        credential_class: str | None,
        minimum_validity_ms: int,
    ) -> dict[str, Any] | None:
        account_id = str(stale_material.get("account_id") or "")
        expected_version = int(stale_material.get("secret_version") or 0)
        owner_id = f"bbrefresh_{uuid.uuid4().hex}"
        lease_duration_ms = max(
            1,
            int(
                getattr(
                    self,
                    "_refresh_lease_ms",
                    (DEFAULT_OAUTH_HTTP_TIMEOUT_SECONDS + 30) * 1_000,
                )
            ),
        )
        poll_seconds = max(
            0.001,
            float(getattr(self, "_refresh_poll_seconds", 0.05)),
        )
        wait_deadline = time.monotonic() + max(
            1.0,
            (lease_duration_ms * 2) / 1000,
        )
        try:
            while True:
                with self.store.atomic():
                    claim = self.store.claim_oauth_refresh(
                        account_id=account_id,
                        expected_secret_version=expected_version,
                        owner_id=owner_id,
                        lease_duration_ms=lease_duration_ms,
                    )
                    claim_status = str(claim.get("status") or "")
                    if claim_status == "acquired":
                        self._emit(
                            "provider_credential_refresh_started",
                            provider_id=provider_id,
                            account_id=account_id,
                            secret_version=expected_version,
                            recovered_stale_lease=bool(
                                claim.get("recovered_stale_lease")
                            ),
                        )
                if claim_status == "acquired":
                    break
                if claim_status == "superseded":
                    return self._acquire_current_account_material(
                        provider_id=provider_id,
                        session_id=session_id,
                        endpoint_id=endpoint_id,
                        account_id=account_id,
                        credential_class=credential_class,
                        minimum_validity_ms=minimum_validity_ms,
                    )
                if claim_status in {"deferred", "unavailable"}:
                    return None
                if claim_status != "busy" or time.monotonic() >= wait_deadline:
                    self._emit(
                        "provider_credential_refresh_wait_ended",
                        provider_id=provider_id,
                        account_id=account_id,
                        status=claim_status or "timeout",
                    )
                    return None
                lease_expires_at_ms = int(
                    claim.get("lease_expires_at_ms") or int(time.time() * 1000)
                )
                remaining_seconds = max(
                    0.001,
                    (lease_expires_at_ms - int(time.time() * 1000)) / 1000,
                )
                time.sleep(min(poll_seconds, remaining_seconds))

            refreshed: dict[str, Any] | None = None
            try:
                if not isinstance(stale_material.get("refresh_token"), str):
                    raise OAuthFlowError(
                        "oauth_refresh_unavailable",
                        "Stored OAuth credential has no refresh token",
                        failure_class="definitive",
                    )
                adapter = self._oauth_adapter(
                    str(stale_material.get("provider_id") or provider_id)
                )
                if adapter is None:
                    raise OAuthFlowError(
                        "flow_unavailable",
                        "OAuth refresh flow is not configured",
                        failure_class="transient",
                    )
                refreshed = self._refresh_with_heartbeat(
                    adapter,
                    stale_material,
                    account_id=account_id,
                    expected_secret_version=expected_version,
                    owner_id=owner_id,
                    lease_duration_ms=lease_duration_ms,
                )
                refreshed_secret_values = redaction.credential_secret_values(refreshed)
                with redaction.secret_value_scope(
                    *refreshed_secret_values,
                    allow_short=True,
                ):
                    refreshed_metadata = {
                        key: stale_material[key]
                        for key in (
                            "email",
                            "provider_account_id",
                            "project_id",
                            "token_type",
                        )
                        if key in stale_material
                    }
                    scrubbed_metadata, _ = redaction.scrub_structure(
                        refreshed_metadata,
                        path="$.metadata",
                    )
                    with self.store.atomic():
                        outcome = self.store.complete_oauth_refresh(
                            account_id=account_id,
                            expected_secret_version=expected_version,
                            owner_id=owner_id,
                            expires_at_ms=int(refreshed["expires_at_ms"]),
                            material=refreshed,
                            metadata=scrubbed_metadata,
                        )
                        outcome_status = str(outcome.get("status") or "")
                        if outcome_status == "completed":
                            credential = outcome.get("credential")
                            self._emit(
                                "provider_credential_refreshed",
                                provider_id=provider_id,
                                account_id=account_id,
                                secret_version=(
                                    credential.get("secret_version")
                                    if isinstance(credential, Mapping)
                                    else expected_version + 1
                                ),
                            )
                        elif outcome_status not in {"superseded"}:
                            self._emit(
                                "provider_credential_refresh_discarded",
                                provider_id=provider_id,
                                account_id=account_id,
                                status=outcome_status or "claim_lost",
                            )
                            return None
                    return self._acquire_current_account_material(
                        provider_id=provider_id,
                        session_id=session_id,
                        endpoint_id=endpoint_id,
                        account_id=account_id,
                        credential_class=credential_class,
                        minimum_validity_ms=minimum_validity_ms,
                    )
            except CredentialAuditPersistenceError:
                raise
            except OAuthFlowError as error:
                self._record_refresh_failure(
                    error,
                    provider_id=provider_id,
                    account_id=account_id,
                    expected_secret_version=expected_version,
                    owner_id=owner_id,
                )
                return None
            except Exception:
                self._record_refresh_failure(
                    OAuthFlowError(
                        "oauth_refresh_unexpected",
                        "OAuth refresh failed unexpectedly",
                        failure_class="transient",
                    ),
                    provider_id=provider_id,
                    account_id=account_id,
                    expected_secret_version=expected_version,
                    owner_id=owner_id,
                )
                return None
            finally:
                if refreshed is not None:
                    self._clear_mutable_material(refreshed)
        finally:
            self._clear_mutable_material(stale_material)

    def _issue_stored_execution_material(
        self,
        provider_id: str,
        *,
        session_id: str = "",
        endpoint_id: str = "",
        account_selector: Any = None,
        credential_class: str | None = None,
        minimum_validity_ms: int = 0,
        bind_explicit_selector: bool = True,
    ) -> dict[str, Any] | None:
        account_id = self._value(account_selector, "accountId", "account_id")
        credential_id = self._value(account_selector, "credentialId", "credential_id")
        label = self._value(account_selector, "accountLabel", "account_label", "label")
        alias = self._value(account_selector, "alias")
        with self.store.atomic():
            material = self.store.acquire_lease(
                provider_id=provider_id,
                session_id=session_id,
                endpoint_id=endpoint_id,
                account_id=str(account_id) if account_id else None,
                credential_id=str(credential_id) if credential_id else None,
                label=str(label) if label else None,
                alias=str(alias) if alias else None,
                credential_class=credential_class,
                minimum_validity_ms=0,
                allow_expired=credential_class in {None, "oauth"},
                bind_explicit=bind_explicit_selector,
            )
            if material is not None and material.get("session_binding_changed"):
                self._emit(
                    "provider_session_account_binding_changed",
                    session_id=str(session_id),
                    provider_id=str(provider_id).strip().lower(),
                    account_id=material.get("account_id"),
                    credential_id=material.get("credential_id"),
                    binding_kind=material.get("session_binding_kind"),
                    reason=material.get("session_binding_reason"),
                )
        if material is None:
            return None
        expires_at = material.get("expires_at_ms")
        is_oauth = str(material.get("credential_kind") or "") == "oauth2"
        required_validity_ms = max(0, int(minimum_validity_ms))
        if is_oauth:
            required_validity_ms = max(required_validity_ms, 30_000)
        now = int(time.time() * 1000)
        needs_refresh = (
            isinstance(expires_at, (int, float))
            and expires_at <= now + required_validity_ms
        )
        if not needs_refresh:
            return material
        if not is_oauth:
            self.store.release_lease(str(material.get("lease_id") or ""))
            self._clear_mutable_material(material)
            return None
        self.store.release_lease(str(material.get("lease_id") or ""))
        refresh_secret_values = redaction.credential_secret_values(material)
        with redaction.secret_value_scope(
            *refresh_secret_values,
            allow_short=True,
        ):
            return self._refresh_stored_material(
                material,
                provider_id=provider_id,
                session_id=session_id,
                endpoint_id=endpoint_id,
                credential_class=credential_class,
                minimum_validity_ms=required_validity_ms,
            )

    @classmethod
    def _copy_owned_value(cls, value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: cls._copy_owned_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [cls._copy_owned_value(item) for item in value]
        if isinstance(value, bytearray):
            return bytearray(value)
        return value

    @classmethod
    def _copy_material(cls, material: Mapping[str, Any]) -> dict[str, Any]:
        return {key: cls._copy_owned_value(value) for key, value in material.items()}

    @staticmethod
    def _account_origin(account: Mapping[str, Any]) -> CredentialOrigin:
        kind = (
            "oauth"
            if str(account.get("credential_kind") or "") == "oauth2"
            else "api_key"
        )
        return CredentialOrigin(
            kind=kind,
            account_id=str(account.get("account_id") or "") or None,
            credential_id=str(account.get("credential_id") or "") or None,
            source=str(account.get("credential_source") or account.get("source") or "")
            or None,
            binding_kind=str(account.get("session_binding_kind") or "") or None,
            binding_reason=str(account.get("session_binding_reason") or "") or None,
        )

    @staticmethod
    def _selector_values(
        account_selector: Any,
    ) -> tuple[str | None, str | None, str | None, str | None]:
        account_id = ProviderBroker._value(
            account_selector,
            "accountId",
            "account_id",
        )
        credential_id = ProviderBroker._value(
            account_selector,
            "credentialId",
            "credential_id",
        )
        label = ProviderBroker._value(
            account_selector,
            "accountLabel",
            "account_label",
            "label",
        )
        alias = ProviderBroker._value(account_selector, "alias")
        return (
            str(account_id) if account_id else None,
            str(credential_id) if credential_id else None,
            str(label) if label else None,
            str(alias) if alias else None,
        )

    def get_session_account_binding(
        self,
        session_id: str,
        provider_id: str,
    ) -> dict[str, Any] | None:
        """Return one durable, secret-free session account binding."""
        return self.store.get_session_account_binding(session_id, provider_id)

    def bind_session_account(
        self,
        session_id: str,
        provider_id: str,
        account_selector: Any,
    ) -> dict[str, Any] | None:
        """Persist an explicit user account choice without reading its secret."""
        account_id, credential_id, label, alias = self._selector_values(
            account_selector
        )
        with self.store.atomic():
            selected = self.store.bind_session_account(
                session_id=session_id,
                provider_id=provider_id,
                account_id=account_id,
                credential_id=credential_id,
                label=label,
                alias=alias,
            )
            if selected is not None:
                self._emit(
                    "provider_session_account_bound",
                    session_id=str(session_id),
                    provider_id=str(provider_id).strip().lower(),
                    account_id=selected["account_id"],
                    credential_id=selected["credential_id"],
                    binding_kind="user",
                )
        return selected

    def clear_session_account_binding(
        self,
        session_id: str,
        provider_id: str,
    ) -> bool:
        """Return a session to deterministic policy selection."""
        with self.store.atomic():
            changed = self.store.clear_session_account_binding(session_id, provider_id)
            if changed:
                self._emit(
                    "provider_session_account_binding_cleared",
                    session_id=str(session_id),
                    provider_id=str(provider_id).strip().lower(),
                )
        return changed

    def _override_copy(
        self,
        target: Mapping[str, Mapping[str, Any]],
        provider_id: str,
    ) -> dict[str, Any] | None:
        with self._lock:
            material = target.get(provider_id)
            return self._copy_material(material) if material is not None else None

    def _has_override(
        self,
        target: Mapping[str, Mapping[str, Any]],
        provider_id: str,
    ) -> bool:
        with self._lock:
            return provider_id in target

    @staticmethod
    def _apply_origin(
        material: dict[str, Any],
        origin: CredentialOrigin,
    ) -> dict[str, Any]:
        material["credential_origin"] = origin.to_dict()
        return material

    def get_credential_origin(
        self,
        provider_id: str,
        *,
        session_id: str = "",
        account_selector: Any = None,
        environment_key: str | None = None,
        environment: Mapping[str, object] | None = None,
    ) -> dict[str, str] | None:
        """Return the selected credential's provenance without secret material."""
        provider = str(provider_id).strip().lower()
        session = str(session_id).strip()
        account_id, credential_id, label, alias = self._selector_values(
            account_selector
        )
        if any((account_id, credential_id, label, alias)):
            account = self.store.select_account_view(
                provider_id=provider,
                account_id=account_id,
                credential_id=credential_id,
                label=label,
                alias=alias,
                session_id=session,
            )
            if account is None:
                account = self.store.select_account_view(
                    provider_id=provider,
                    account_id=account_id,
                    credential_id=credential_id,
                    label=label,
                    alias=alias,
                    credential_class="oauth",
                    session_id=session,
                    allow_expired=True,
                )
            return self._account_origin(account).to_dict() if account else None
        binding = (
            self.store.get_session_account_binding(session, provider)
            if session
            else None
        )
        if binding is not None and binding.get("binding_kind") == "user":
            account = self.store.select_account_view(
                provider_id=provider,
                account_id=str(binding["account_id"]),
                session_id=session,
            )
            if account is None:
                account = self.store.select_account_view(
                    provider_id=provider,
                    account_id=str(binding["account_id"]),
                    credential_class="oauth",
                    session_id=session,
                    allow_expired=True,
                )
            return self._account_origin(account).to_dict() if account else None
        if self._has_override(self._runtime_overrides, provider):
            return CredentialOrigin(kind="runtime").to_dict()
        if self._has_override(self._config_overrides, provider):
            return CredentialOrigin(kind="config").to_dict()
        for credential_class in ("oauth", "login_api_key"):
            account = self.store.select_account_view(
                provider_id=provider,
                credential_class=credential_class,
                session_id=session,
                allow_expired=credential_class == "oauth",
            )
            if account is not None:
                return self._account_origin(account).to_dict()
        source = os.environ if environment is None else environment
        env_value = source.get(environment_key) if environment_key else None
        if isinstance(env_value, str) and env_value.strip():
            return CredentialOrigin(
                kind="env",
                env_var=environment_key,
            ).to_dict()
        account = self.store.select_account_view(
            provider_id=provider,
            credential_class="stored_api_key",
            session_id=session,
        )
        if account is not None:
            return self._account_origin(account).to_dict()
        fallback_source = self._fallback_origin_source(provider)
        if fallback_source:
            return CredentialOrigin(
                kind="fallback",
                source=fallback_source,
            ).to_dict()
        return None

    def _issue_execution_material(
        self,
        provider_id: str,
        *,
        session_id: str = "",
        endpoint_id: str = "",
        account_selector: Any = None,
        environment_key: str | None = None,
        environment: Mapping[str, object] | None = None,
        minimum_validity_ms: int = 0,
    ) -> dict[str, Any] | None:
        provider = str(provider_id).strip().lower()
        session = str(session_id).strip()
        account_id, credential_id, label, alias = self._selector_values(
            account_selector
        )
        if any((account_id, credential_id, label, alias)):
            selected = self._issue_stored_execution_material(
                provider,
                session_id=session,
                endpoint_id=endpoint_id,
                account_selector={
                    "account_id": account_id,
                    "credential_id": credential_id,
                    "label": label,
                    "alias": alias,
                },
                minimum_validity_ms=minimum_validity_ms,
            )
            if selected is not None:
                return self._apply_origin(
                    selected,
                    self._account_origin(selected),
                )
            return None
        binding = (
            self.store.get_session_account_binding(session, provider)
            if session
            else None
        )
        if binding is not None and binding.get("binding_kind") == "user":
            selected = self._issue_stored_execution_material(
                provider,
                session_id=session,
                endpoint_id=endpoint_id,
                account_selector={"account_id": binding["account_id"]},
                minimum_validity_ms=minimum_validity_ms,
                bind_explicit_selector=False,
            )
            if selected is not None:
                return self._apply_origin(
                    selected,
                    self._account_origin(selected),
                )
            return None
        for kind, target in (
            ("runtime", self._runtime_overrides),
            ("config", self._config_overrides),
        ):
            override = self._override_copy(target, provider)
            if override is not None:
                return self._apply_origin(
                    override,
                    CredentialOrigin(kind=kind),
                )
        for credential_class in ("oauth", "login_api_key"):
            stored = self._issue_stored_execution_material(
                provider,
                session_id=session,
                endpoint_id=endpoint_id,
                credential_class=credential_class,
                minimum_validity_ms=minimum_validity_ms,
            )
            if stored is not None:
                return self._apply_origin(
                    stored,
                    self._account_origin(stored),
                )
        source = os.environ if environment is None else environment
        env_value = source.get(environment_key) if environment_key else None
        if isinstance(env_value, str) and env_value.strip():
            return self._apply_origin(
                {"api_key": env_value.strip()},
                CredentialOrigin(kind="env", env_var=environment_key),
            )
        stored = self._issue_stored_execution_material(
            provider,
            session_id=session,
            endpoint_id=endpoint_id,
            credential_class="stored_api_key",
            minimum_validity_ms=minimum_validity_ms,
        )
        if stored is not None:
            return self._apply_origin(
                stored,
                self._account_origin(stored),
            )
        fallback = self._load_fallback_material(provider)
        if fallback is None:
            return None
        api_key = fallback.get("api_key") or fallback.get("access_token")
        if not isinstance(api_key, str) or not api_key.strip():
            self._clear_mutable_material(fallback)
            return None
        fallback["api_key"] = api_key.strip()
        origin_source = (
            "codex_auth_file"
            if fallback.pop("_origin_source", None) == "codex_auth_file"
            else "resolver"
        )
        with self._lock:
            self._fallback_origins[provider] = origin_source
        return self._apply_origin(
            fallback,
            CredentialOrigin(kind="fallback", source=origin_source),
        )

    @staticmethod
    def _rate_limit_deadline_ms(error: BaseException) -> int | None:
        details = getattr(error, "details", None)
        if not isinstance(details, Mapping):
            return None
        classification = str(details.get("classification") or "").lower()
        status = details.get("status_code", details.get("http_status"))
        try:
            status_code = int(status)
        except (TypeError, ValueError):
            return None
        if classification != "rate_limited" or status_code != 429:
            return None
        retry_after: Any = details.get(
            "retry_after_seconds",
            details.get("retry_after"),
        )
        headers = details.get("response_headers")
        if retry_after is None and isinstance(headers, Mapping):
            retry_after = headers.get("retry-after")
        try:
            seconds = float(retry_after) if retry_after is not None else 60.0
        except (TypeError, ValueError):
            seconds = 60.0
        bounded_seconds = min(3600.0, max(1.0, seconds))
        return int(time.time() * 1000 + bounded_seconds * 1000)

    @contextmanager
    def execution_material(
        self,
        provider_id: str,
        *,
        session_id: str = "",
        endpoint_id: str = "",
        account_selector: Any = None,
        environment_key: str | None = None,
        environment: Mapping[str, object] | None = None,
        minimum_validity_ms: int = 0,
    ):
        """Yield broker material for exactly one SDK operation and always release it."""
        material = self._issue_execution_material(
            provider_id,
            session_id=session_id,
            endpoint_id=endpoint_id,
            account_selector=account_selector,
            environment_key=environment_key,
            environment=environment,
            minimum_validity_ms=minimum_validity_ms,
        )
        secret_values = redaction.credential_secret_values(material)
        with redaction.secret_value_scope(
            *secret_values,
            allow_short=True,
        ):
            try:
                yield material
            except BaseException as error:
                blocked_until_ms = self._rate_limit_deadline_ms(error)
                account_id = (
                    str(material.get("account_id") or "")
                    if isinstance(material, dict)
                    else ""
                )
                if blocked_until_ms is not None and account_id:
                    with self.store.atomic():
                        self.store.mark_account_rate_limited(
                            account_id,
                            blocked_until_ms,
                        )
                        self._emit(
                            "provider_account_rate_limited",
                            provider_id=str(provider_id).strip().lower(),
                            session_id=str(session_id),
                            account_id=account_id,
                            blocked_until_ms=blocked_until_ms,
                        )
                raise
            finally:
                try:
                    if isinstance(material, dict):
                        lease_id = material.get("lease_id")
                        if lease_id:
                            self._release_execution_material(str(lease_id))
                finally:
                    if isinstance(material, dict):
                        self._clear_mutable_material(material)

    def _release_execution_material(self, lease_id: str) -> bool:
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
    """Return the local broker only when no remote broker was requested."""
    if any(
        (os.environ.get(name) or "").strip()
        for name in (REMOTE_BROKER_URL_ENV, REMOTE_BROKER_CONFIGURED_ENV)
    ):
        raise ProviderBrokerConfigurationError(
            "remote provider broker is configured but no remote transport is available"
        )
    global _default_broker
    with _default_lock:
        if _default_broker is None:
            _default_broker = ProviderBroker()
        return _default_broker


provider_broker = get_provider_broker
