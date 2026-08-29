"""Provider OAuth adapters built only from source-established flow specs."""

from __future__ import annotations

import base64
import hashlib
import json
import math
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

from breadboard_engine.security.redaction import MIN_REGISTERED_SECRET_LENGTH
from .catalog import OAuthFlowSpec

DEFAULT_OAUTH_HTTP_TIMEOUT_SECONDS = 30


class OAuthTransport(Protocol):
    def __call__(self, url: str, *, method: str, headers: Mapping[str, str], body: bytes | None = None) -> tuple[int, Mapping[str, str], bytes]: ...


def default_oauth_transport(
    url: str,
    *,
    method: str,
    headers: Mapping[str, str],
    body: bytes | None = None,
) -> tuple[int, Mapping[str, str], bytes]:
    request = urllib.request.Request(url, data=body, headers=dict(headers), method=method)
    try:
        with urllib.request.urlopen(
            request,
            timeout=DEFAULT_OAUTH_HTTP_TIMEOUT_SECONDS,
        ) as response:
            return int(response.status), dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as error:
        return int(error.code), dict(error.headers.items()), error.read()


@dataclass(frozen=True)
class OAuthLoginStart:
    public: dict[str, Any]
    internal: dict[str, Any]


class OAuthFlowError(ValueError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def _json_body(status: int, body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OAuthFlowError("oauth_invalid_response", "OAuth endpoint returned invalid JSON", status=status) from error
    if not isinstance(value, dict):
        raise OAuthFlowError("oauth_invalid_response", "OAuth endpoint returned a non-object response", status=status)
    return value


def _refresh_error_code(body: bytes) -> str | None:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    value = payload.get("error")
    if isinstance(value, Mapping):
        value = value.get("code") or value.get("type")
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if not normalized or len(normalized) > 64:
        return None
    if any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for character in normalized):
        return None
    return normalized


def _refresh_failure_class(status: int, error_code: str | None) -> str:
    if error_code in {
        "invalid_client",
        "invalid_grant",
        "invalid_token",
        "revoked_token",
        "unauthorized_client",
    }:
        return "definitive"
    if error_code in {
        "rate_limit",
        "rate_limited",
        "server_error",
        "temporarily_unavailable",
    }:
        return "transient"
    return "definitive" if int(status) in {400, 401, 403} else "transient"


def _header_value(headers: Mapping[str, str], name: str) -> str | None:
    expected = name.lower()
    for key, value in headers.items():
        if str(key).lower() == expected and value is not None:
            rendered = str(value).strip()
            return rendered[:128] if rendered else None
    return None


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    return verifier, challenge


def _jwt_claim(token: str, path: tuple[str, ...]) -> str | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + "=" * (-len(parts[1]) % 4)).decode())
        value: Any = payload
        for key in path:
            value = value.get(key) if isinstance(value, Mapping) else None
        return str(value) if isinstance(value, str) and value else None
    except (ValueError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
        return None


def _is_valid_oauth_token(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value.strip()) >= MIN_REGISTERED_SECRET_LENGTH
    )


class OAuthFlowAdapter:
    def __init__(self, spec: OAuthFlowSpec, *, transport: OAuthTransport | None = None) -> None:
        self.spec = spec
        self.client_id = spec.resolved_client_id()
        self.transport = transport or default_oauth_transport

    def _require_client_id(self) -> str:
        if self.client_id:
            return self.client_id
        raise OAuthFlowError(
            "flow_unavailable",
            f"OAuth client is not configured for '{self.spec.flow_id}'.",
            configuration_key=self.spec.client_id_env,
        )

    def begin(self, *, flow_kind: str = "browser") -> OAuthLoginStart:
        client_id = self._require_client_id()
        state = secrets.token_urlsafe(24)
        if flow_kind == "device":
            if not self.spec.device_usercode_url:
                raise OAuthFlowError("flow_unavailable", f"No device flow is established for '{self.spec.flow_id}'.")
            status, _headers, raw = self.transport(
                self.spec.device_usercode_url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=json.dumps({"client_id": client_id}).encode(),
            )
            if status < 200 or status >= 300:
                raise OAuthFlowError("oauth_device_start_failed", "Device authorization initiation failed", status=status)
            data = _json_body(status, raw)
            device_id, user_code = data.get("device_auth_id"), data.get("user_code")
            expires = data.get("expires_in", 600)
            try:
                expires_seconds = float(expires)
            except (TypeError, ValueError):
                expires_seconds = 0.0
            if (
                not isinstance(device_id, str)
                or not isinstance(user_code, str)
                or isinstance(expires, bool)
                or not math.isfinite(expires_seconds)
                or expires_seconds <= 0
                or expires_seconds > 31_536_000
            ):
                raise OAuthFlowError(
                    "oauth_invalid_response",
                    "Device response has invalid identity or expiry fields",
                    status=status,
                )
            internal = {
                "flow_id": self.spec.flow_id,
                "flow_kind": "device",
                "state": state,
                "device_auth_id": device_id,
                "user_code": user_code,
                "interval": data.get("interval", 5),
                "expires_in": expires_seconds,
            }
            return OAuthLoginStart(
                public={"flow_id": self.spec.flow_id, "flow_kind": "device", "authorization_url": self.spec.device_auth_url, "user_code": user_code, "instructions": f"Enter code: {user_code}"},
                internal=internal,
            )
        if flow_kind != "browser":
            raise OAuthFlowError("flow_unavailable", f"Unknown OAuth flow kind '{flow_kind}'.")
        verifier, challenge = _pkce()
        callback_host = "127.0.0.1" if self.spec.flow_id.startswith("google-") else "localhost"
        redirect_uri = f"http://{callback_host}:{self.spec.callback_port}{self.spec.callback_path}"
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": " ".join(self.spec.scopes),
            "code_challenge": challenge,
            "code_challenge_method": "S256",
            "state": state,
        }
        if self.spec.flow_id == "openai-codex":
            params.update({"id_token_add_organizations": "true", "codex_cli_simplified_flow": "true", "originator": "pi"})
        elif self.spec.flow_id.startswith("google-"):
            params.update({"access_type": "offline", "prompt": "consent"})
        elif self.spec.flow_id == "anthropic":
            params["code"] = "true"
        internal = {"flow_id": self.spec.flow_id, "flow_kind": "browser", "state": state, "verifier": verifier, "redirect_uri": redirect_uri}
        return OAuthLoginStart(
            public={"flow_id": self.spec.flow_id, "flow_kind": "browser", "authorization_url": f"{self.spec.auth_url}?{urllib.parse.urlencode(params)}", "redirect_uri": redirect_uri},
            internal=internal,
        )

    def complete(
        self,
        flow: Mapping[str, Any],
        *,
        code: str | None = None,
        state: str | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        flow_kind = str(flow.get("flow_kind") or "browser")
        if flow_kind == "device":
            return self._complete_device(flow, is_cancelled=is_cancelled)
        expected_state = str(flow.get("state") or "")
        if not code or not state or not secrets.compare_digest(expected_state, str(state)):
            raise OAuthFlowError("oauth_state_mismatch", "OAuth callback state did not match")
        body_values = {
            "grant_type": "authorization_code",
            "client_id": self._require_client_id(),
            "code": str(code).split("#", 1)[0],
            "redirect_uri": str(flow.get("redirect_uri") or ""),
            "code_verifier": str(flow.get("verifier") or ""),
        }
        if self.spec.flow_id == "anthropic":
            body_values["state"] = str(state)
        headers = {"Content-Type": "application/json"} if self.spec.flow_id == "anthropic" else {"Content-Type": "application/x-www-form-urlencoded"}
        body = json.dumps(body_values).encode() if self.spec.flow_id == "anthropic" else urllib.parse.urlencode(body_values).encode()
        status, _response_headers, raw = self.transport(self.spec.token_url, method="POST", headers=headers, body=body)
        if status < 200 or status >= 300:
            raise OAuthFlowError("oauth_token_exchange_failed", "OAuth token exchange failed", status=status)
        return self._token_material(_json_body(status, raw))

    def _complete_device(
        self,
        flow: Mapping[str, Any],
        *,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if not self.spec.device_token_url:
            raise OAuthFlowError("flow_unavailable", "Device token endpoint is not established")
        interval = max(1.0, float(flow.get("interval") or 5))
        deadline = time.time() + float(flow.get("expires_in") or 600)
        def wait_for_next_poll(seconds: float) -> None:
            wait_deadline = time.time() + seconds
            while True:
                remaining = wait_deadline - time.time()
                if remaining <= 0:
                    return
                time.sleep(min(30.0, remaining))
                if is_cancelled is not None and is_cancelled():
                    raise OAuthFlowError(
                        "oauth_login_cancelled", "OAuth login was cancelled"
                    )
        while time.time() < deadline:
            if is_cancelled is not None and is_cancelled():
                raise OAuthFlowError("oauth_login_cancelled", "OAuth login was cancelled")
            status, _headers, raw = self.transport(
                self.spec.device_token_url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=json.dumps({"device_auth_id": flow.get("device_auth_id"), "user_code": flow.get("user_code")}).encode(),
            )
            if is_cancelled is not None and is_cancelled():
                raise OAuthFlowError("oauth_login_cancelled", "OAuth login was cancelled")
            if status in {403, 404}:
                wait_for_next_poll(min(interval, max(0.0, deadline - time.time())))
                continue
            if status < 200 or status >= 300:
                raise OAuthFlowError("oauth_device_poll_failed", "Device token polling failed", status=status)
            data = _json_body(status, raw)
            auth_code, verifier = data.get("authorization_code"), data.get("code_verifier")
            if not isinstance(auth_code, str) or not isinstance(verifier, str):
                raise OAuthFlowError("oauth_invalid_response", "Device response missing authorization_code or code_verifier")
            next_flow = dict(flow)
            next_flow.update({"flow_kind": "browser", "code_verifier": verifier, "verifier": verifier, "redirect_uri": self.spec.device_redirect_uri, "state": flow.get("state")})
            return self._exchange_device_code(auth_code, verifier)
        raise OAuthFlowError("oauth_device_timeout", "Device authorization timed out")

    def _exchange_device_code(self, code: str, verifier: str) -> dict[str, Any]:
        values = {"grant_type": "authorization_code", "client_id": self._require_client_id(), "code": code, "code_verifier": verifier, "redirect_uri": self.spec.device_redirect_uri or ""}
        status, _headers, raw = self.transport(self.spec.token_url, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"}, body=urllib.parse.urlencode(values).encode())
        if status < 200 or status >= 300:
            raise OAuthFlowError("oauth_token_exchange_failed", "OAuth token exchange failed", status=status)
        return self._token_material(_json_body(status, raw))

    @staticmethod
    def _token_material(
        data: Mapping[str, Any],
        *,
        require_refresh: bool = True,
    ) -> dict[str, Any]:
        access = data.get("access_token")
        refresh = data.get("refresh_token")
        expires = data.get("expires_in")
        try:
            expires_seconds = float(expires)
        except (TypeError, ValueError):
            expires_seconds = float("nan")
        malformed = (
            not _is_valid_oauth_token(access)
            or isinstance(expires, bool)
            or not math.isfinite(expires_seconds)
            or expires_seconds <= 0
            or expires_seconds > 31_536_000
            or (require_refresh and refresh is None)
            or (refresh is not None and not _is_valid_oauth_token(refresh))
        )
        if malformed:
            raise OAuthFlowError(
                "oauth_invalid_response",
                "Token response has invalid access_token, refresh_token, or expires_in",
            )
        result: dict[str, Any] = {
            "access_token": access,
            "token_type": data.get("token_type", "Bearer"),
            "expires_at_ms": int(
                time.time() * 1000 + expires_seconds * 1000
            ),
        }
        if refresh:
            result["refresh_token"] = refresh
        account_id = _jwt_claim(access, ("https://api.openai.com/auth", "chatgpt_account_id"))
        email = _jwt_claim(access, ("https://api.openai.com/profile", "email"))
        if account_id:
            result["provider_account_id"] = account_id
        if email:
            result["email"] = email
        for key in ("projectId", "project_id"):
            if isinstance(data.get(key), str):
                result["project_id"] = data[key]
        return result

    def refresh(self, material: Mapping[str, Any]) -> dict[str, Any]:
        refresh_token = material.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise OAuthFlowError(
                "oauth_refresh_unavailable",
                "Stored OAuth credential has no refresh token",
                failure_class="definitive",
            )
        values = {"grant_type": "refresh_token", "client_id": self._require_client_id(), "refresh_token": refresh_token}
        headers = {"Content-Type": "application/json"} if self.spec.flow_id == "anthropic" else {"Content-Type": "application/x-www-form-urlencoded"}
        if self.spec.flow_id == "anthropic":
            headers.update({"anthropic-beta": "oauth-2025-04-20", "User-Agent": "anthropic-sdk-typescript/0.94.0 userOAuthProvider"})
        body = json.dumps(values).encode() if self.spec.flow_id == "anthropic" else urllib.parse.urlencode(values).encode()
        try:
            status, response_headers, raw = self.transport(
                self.spec.token_url,
                method="POST",
                headers=headers,
                body=body,
            )
        except Exception:
            raise OAuthFlowError(
                "oauth_refresh_transport_failed",
                "OAuth token refresh transport failed",
                failure_class="transient",
            ) from None
        if status < 200 or status >= 300:
            error_code = _refresh_error_code(raw)
            details: dict[str, Any] = {
                "status": int(status),
                "failure_class": _refresh_failure_class(status, error_code),
            }
            if error_code:
                details["oauth_error"] = error_code
            retry_after = _header_value(response_headers, "retry-after")
            if retry_after:
                details["retry_after"] = retry_after
            raise OAuthFlowError(
                "oauth_refresh_failed",
                "OAuth token refresh failed",
                **details,
            )
        try:
            result = self._token_material(
                _json_body(status, raw),
                require_refresh=False,
            )
        except OAuthFlowError as error:
            error.details.setdefault("failure_class", "transient")
            raise
        if not result.get("refresh_token"):
            result["refresh_token"] = refresh_token
        return result
