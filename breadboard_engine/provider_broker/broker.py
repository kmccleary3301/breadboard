"""In-process provider credential broker.

The public broker surface deliberately exchanges JSON-like values only.  Secret
material is held by the SQLite store and is issued narrowly to provider
routing; credential listings and audit records contain metadata only.
"""

import os
import queue
import secrets
import threading
import time
from typing import Any, Callable, Mapping

from breadboard_engine.security import redaction

from .store import SQLiteCredentialStore
from .catalog import get_provider_catalog_entry, provider_catalog
from .oauth import OAuthFlowAdapter, OAuthFlowError, OAuthTransport


_CREDENTIAL_ENV_MARKERS = (
    "ACCESS_KEY",
    "API_KEY",
    "AUTH_TOKEN",
    "CREDENTIAL",
    "PASSWORD",
    "PRIVATE_KEY",
    "SECRET",
    "TOKEN",
)
_CREDENTIAL_ENV_SUFFIXES = ("AUTH_HEADERS_JSON", "AUTH_BASE_URL")
_SENSITIVE_CAPABILITY_ENV_NAMES = frozenset(
    {
        "AWS_PROFILE",
        "AWS_SHARED_CREDENTIALS_FILE",
        "AZURE_CONFIG_DIR",
        "DOCKER_CONFIG",
        "DOCKER_HOST",
        "GIT_ASKPASS",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GPG_AGENT_INFO",
        "KUBECONFIG",
        "NETRC",
        "SSH_AGENT_PID",
        "SSH_ASKPASS",
        "SSH_AUTH_SOCK",
    }
)
_CHILD_ENV_ALLOWLIST = frozenset(
    {
        "BREADBOARD_STATE_DIR",
        "COLORTERM",
        "CURL_CA_BUNDLE",
        "HOME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "LOGNAME",
        "MKL_NUM_THREADS",
        "NO_COLOR",
        "NUMEXPR_NUM_THREADS",
        "OMP_NUM_THREADS",
        "OPENBLAS_NUM_THREADS",
        "PATH",
        "PRESERVE_SEEDED_WORKSPACE",
        "PYTHONHASHSEED",
        "PYTHONIOENCODING",
        "PYTHONUNBUFFERED",
        "REQUESTS_CA_BUNDLE",
        "SHELL",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "TEMP",
        "TERM",
        "TMP",
        "TMPDIR",
        "USER",
        "VECLIB_MAXIMUM_THREADS",
    }
)


def _is_credential_environment_name(name: str) -> bool:
    normalized = str(name).strip().upper().replace("-", "_")
    return (
        normalized in _SENSITIVE_CAPABILITY_ENV_NAMES
        or redaction.is_secret_key(name)
        or any(marker in normalized for marker in _CREDENTIAL_ENV_MARKERS)
        or normalized.endswith(_CREDENTIAL_ENV_SUFFIXES)
    )


def credential_environment_violations(
    environment: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return non-empty ambient credential or authority-capability names."""
    source = os.environ if environment is None else environment
    return sorted(
        str(name)
        for name, value in source.items()
        if str(value) and _is_credential_environment_name(str(name))
    )


def _is_allowed_child_environment_name(name: str) -> bool:
    normalized = str(name).strip().upper()
    return (
        normalized in _CHILD_ENV_ALLOWLIST
        and not _is_credential_environment_name(normalized)
    )


def child_environment_violations(
    environment: Mapping[str, Any] | None = None,
) -> list[str]:
    """Return non-empty variables outside the explicit worker allowlist."""
    source = os.environ if environment is None else environment
    return sorted(
        str(name)
        for name, value in source.items()
        if str(value) and not _is_allowed_child_environment_name(str(name))
    )


def project_child_environment(
    overrides: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    """Build the explicit environment allowlist for an untrusted worker."""
    projected = {
        name: value
        for name, value in os.environ.items()
        if _is_allowed_child_environment_name(name)
    }
    override_values = {str(name): value for name, value in (overrides or {}).items()}
    for key, value in override_values.items():
        if _is_allowed_child_environment_name(key):
            projected[key] = str(value)
        elif _is_credential_environment_name(key):
            projected[key] = ""
    state_dir = projected.get("BREADBOARD_STATE_DIR")
    if state_dir:
        projected["HOME"] = state_dir
        projected["TMPDIR"] = state_dir
    return projected


def scrub_child_environment() -> None:
    """Delete every worker variable outside the explicit allowlist."""
    for name in tuple(os.environ):
        if not _is_allowed_child_environment_name(name):
            os.environ.pop(name, None)


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
            {"event": event, "timestamp_ms": time.time_ns() // 1_000_000, **fields}
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
            redaction.register_secret_value(material.get("api_key"))
            redaction.register_secret_value(material.get("access_token"))
            redaction.register_secret_value(material.get("refresh_token"))
            for value in dict(material.get("headers") or {}).values():
                redaction.register_secret_value(value)
        return dict(material) if material is not None else None

    def redeem_execution_material(
        self,
        lease_id: str,
        *,
        provider_id: str,
        endpoint_id: str = "",
    ) -> dict[str, Any] | None:
        material = self.store.redeem_lease(
            lease_id=lease_id,
            provider_id=provider_id,
            endpoint_id=endpoint_id,
        )
        if material is None:
            return None
        redaction.register_secret_value(material.get("api_key"))
        redaction.register_secret_value(material.get("access_token"))
        redaction.register_secret_value(material.get("refresh_token"))
        for value in dict(material.get("headers") or {}).values():
            redaction.register_secret_value(value)
        self._emit(
            "provider_lease_redeemed",
            provider_id=provider_id,
            account_id=material.get("account_id"),
            lease_id=lease_id,
        )
        return dict(material)

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


class LeaseCapabilityChannel:
    """Worker-side fixed protocol for one already-issued execution lease."""

    def __init__(
        self,
        *,
        request_queue: Any,
        response_queue: Any,
        capability_token: str,
        provider_id: str,
        endpoint_id: str,
        timeout_s: float = 30.0,
    ) -> None:
        self._request_queue = request_queue
        self._response_queue = response_queue
        self._capability_token = capability_token
        self._provider_id = provider_id
        self._endpoint_id = endpoint_id
        self._timeout_s = timeout_s

    def redeem(self, *, provider_id: str, endpoint_id: str) -> dict[str, Any] | None:
        if provider_id != self._provider_id or endpoint_id != self._endpoint_id:
            return None
        request_id = secrets.token_urlsafe(18)
        self._request_queue.put(
            {
                "request_id": request_id,
                "capability_token": self._capability_token,
                "operation": "redeem",
                "provider_id": provider_id,
                "endpoint_id": endpoint_id,
            },
            timeout=self._timeout_s,
        )
        deadline = time.monotonic() + self._timeout_s
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                response = self._response_queue.get(timeout=remaining)
            except queue.Empty:
                return None
            if not isinstance(response, Mapping) or response.get("request_id") != request_id:
                continue
            material = response.get("material")
            return dict(material) if isinstance(material, Mapping) else None


class LeaseCapabilityServer:
    """Driver-owned fixed dispatcher; its broker is never serialized to workers."""

    def __init__(
        self,
        *,
        broker: ProviderBroker,
        request_queue: Any,
        response_queue: Any,
        capability_token: str,
        lease_id: str,
        provider_id: str,
        endpoint_id: str,
    ) -> None:
        self._broker = broker
        self._request_queue = request_queue
        self._response_queue = response_queue
        self._capability_token = capability_token
        self._lease_id = lease_id
        self._provider_id = provider_id
        self._endpoint_id = endpoint_id
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._serve,
            name=f"breadboard-provider-lease-{lease_id[-8:]}",
            daemon=True,
        )

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=2)
        for transport in (self._request_queue, self._response_queue):
            shutdown = getattr(transport, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown(force=True)
                except Exception:
                    pass

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                request = self._request_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            if not isinstance(request, Mapping):
                continue
            request_id = request.get("request_id")
            material: dict[str, Any] | None = None
            if (
                isinstance(request_id, str)
                and request.get("capability_token") == self._capability_token
                and request.get("operation") == "redeem"
                and request.get("provider_id") == self._provider_id
                and request.get("endpoint_id") == self._endpoint_id
            ):
                try:
                    material = self._broker.redeem_execution_material(
                        self._lease_id,
                        provider_id=self._provider_id,
                        endpoint_id=self._endpoint_id,
                    )
                except Exception:
                    material = None
            try:
                self._response_queue.put(
                    {"request_id": request_id, "material": material},
                    timeout=0.1,
                )
            except queue.Full:
                continue


def _scrub_ray_actor_environment(actor: Any) -> dict[str, Any]:
    """Scrub one already-started Ray actor after Ray applies its own environment."""
    del actor
    scrub_child_environment()
    state_dir = os.environ.get("BREADBOARD_STATE_DIR")
    return {
        "environment_violations": child_environment_violations(),
        "credential_environment_violations": credential_environment_violations(),
        "home_is_isolated": bool(state_dir) and os.environ.get("HOME") == state_dir,
        "tmpdir_is_isolated": bool(state_dir) and os.environ.get("TMPDIR") == state_dir,
    }


def _confine_ray_queue_environment(transport: Any) -> None:
    """Scrub a Ray queue actor before its handle crosses into a worker."""
    import ray

    actor = getattr(transport, "actor", None)
    direct_call = getattr(actor, "__ray_call__", None)
    remote = getattr(direct_call, "remote", None)
    if not callable(remote):
        raise RuntimeError("Ray queue actor does not expose a confinement call")
    observation = ray.get(remote(_scrub_ray_actor_environment))
    if not isinstance(observation, Mapping):
        raise RuntimeError("Ray queue actor returned no confinement observation")
    if (
        observation.get("environment_violations")
        or observation.get("credential_environment_violations")
        or observation.get("home_is_isolated") is not True
        or observation.get("tmpdir_is_isolated") is not True
    ):
        raise RuntimeError(
            "Ray queue actor environment confinement failed: "
            f"{dict(observation)}"
        )


def start_lease_capability_channel(
    broker: ProviderBroker,
    *,
    lease_id: str,
    provider_id: str,
    endpoint_id: str,
    worker_state_dir: str,
) -> tuple[LeaseCapabilityChannel, LeaseCapabilityServer]:
    """Start isolated Ray transports while retaining broker authority in the driver."""
    from ray.util.queue import Queue

    actor_options = {
        "num_cpus": 0,
        "runtime_env": {
            "env_vars": project_child_environment(
                {
                    "BREADBOARD_CREDENTIAL_STORE_PATH": "",
                    "BREADBOARD_CREDENTIAL_DB": "",
                    "BREADBOARD_STATE_DIR": worker_state_dir,
                    "HOME": worker_state_dir,
                    "TMPDIR": worker_state_dir,
                }
            ),
            "worker_process_setup_hook": (
                "breadboard_engine.provider_broker.broker.scrub_child_environment"
            ),
        },
    }
    request_queue = None
    response_queue = None
    try:
        request_queue = Queue(maxsize=8, actor_options=actor_options)
        _confine_ray_queue_environment(request_queue)
        response_queue = Queue(maxsize=8, actor_options=actor_options)
        _confine_ray_queue_environment(response_queue)
    except Exception:
        for transport in (request_queue, response_queue):
            shutdown = getattr(transport, "shutdown", None)
            if callable(shutdown):
                try:
                    shutdown(force=True)
                except Exception:
                    pass
        raise
    capability_token = secrets.token_urlsafe(32)
    channel = LeaseCapabilityChannel(
        request_queue=request_queue,
        response_queue=response_queue,
        capability_token=capability_token,
        provider_id=provider_id,
        endpoint_id=endpoint_id,
    )
    server = LeaseCapabilityServer(
        broker=broker,
        request_queue=request_queue,
        response_queue=response_queue,
        capability_token=capability_token,
        lease_id=lease_id,
        provider_id=provider_id,
        endpoint_id=endpoint_id,
    )
    server.start()
    return channel, server


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
