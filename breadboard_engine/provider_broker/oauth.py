"""Provider OAuth adapters built only from source-established flow specs."""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping, Protocol

from .catalog import OAuthFlowSpec


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
        with urllib.request.urlopen(request, timeout=30) as response:
            return int(response.status), dict(response.headers.items()), response.read()
    except urllib.error.HTTPError as error:
        return _http_error_response(error)
    except (urllib.error.URLError, TimeoutError) as error:
        raise _transport_failure(error) from error


@dataclass(frozen=True)
class OAuthLoginStart:
    public: dict[str, Any]
    internal: dict[str, Any]


class OAuthFlowError(ValueError):
    def __init__(self, code: str, message: str, **details: Any) -> None:
        super().__init__(message)
        self.code = code
        self.details = details


def _transport_failure(
    error: urllib.error.URLError | TimeoutError,
) -> OAuthFlowError:
    cause = error.reason if isinstance(error, urllib.error.URLError) else error
    cause_type = type(cause).__name__
    if isinstance(cause, TimeoutError):
        return OAuthFlowError(
            "oauth_transport_timeout",
            "OAuth endpoint request timed out",
            cause_type=cause_type,
        )
    return OAuthFlowError(
        "oauth_transport_error",
        "OAuth endpoint request failed",
        cause_type=cause_type,
    )


def _http_error_response(
    error: urllib.error.HTTPError,
) -> tuple[int, Mapping[str, str], bytes]:
    response_headers = (
        dict(error.headers.items()) if error.headers is not None else {}
    )
    try:
        body = error.read()
    except (urllib.error.URLError, TimeoutError) as body_error:
        raise _transport_failure(body_error) from body_error
    return int(error.code), response_headers, body


def _json_body(status: int, body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OAuthFlowError("oauth_invalid_response", "OAuth endpoint returned invalid JSON", status=status) from error
    if not isinstance(value, dict):
        raise OAuthFlowError("oauth_invalid_response", "OAuth endpoint returned a non-object response", status=status)
    return value


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


class OAuthFlowAdapter:
    def __init__(self, spec: OAuthFlowSpec, *, transport: OAuthTransport | None = None) -> None:
        self.spec = spec
        self.client_id = spec.resolved_client_id()
        self.transport = transport or default_oauth_transport

    def _request(
        self,
        url: str,
        *,
        method: str,
        headers: Mapping[str, str],
        body: bytes | None = None,
    ) -> tuple[int, Mapping[str, str], bytes]:
        try:
            return self.transport(url, method=method, headers=headers, body=body)
        except urllib.error.HTTPError as error:
            return _http_error_response(error)
        except (urllib.error.URLError, TimeoutError) as error:
            raise _transport_failure(error) from error

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
            status, _headers, raw = self._request(
                self.spec.device_usercode_url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=json.dumps({"client_id": client_id}).encode(),
            )
            if status < 200 or status >= 300:
                raise OAuthFlowError("oauth_device_start_failed", "Device authorization initiation failed", status=status)
            data = _json_body(status, raw)
            device_id, user_code = data.get("device_auth_id"), data.get("user_code")
            if not isinstance(device_id, str) or not isinstance(user_code, str):
                raise OAuthFlowError("oauth_invalid_response", "Device response missing device_auth_id or user_code", status=status)
            internal = {
                "flow_id": self.spec.flow_id,
                "flow_kind": "device",
                "state": state,
                "device_auth_id": device_id,
                "user_code": user_code,
                "interval": data.get("interval", 5),
                "expires_in": data.get("expires_in", 600),
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

    def complete(self, flow: Mapping[str, Any], *, code: str | None = None, state: str | None = None) -> dict[str, Any]:
        flow_kind = str(flow.get("flow_kind") or "browser")
        if flow_kind == "device":
            return self._complete_device(flow)
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
        status, _response_headers, raw = self._request(self.spec.token_url, method="POST", headers=headers, body=body)
        if status < 200 or status >= 300:
            raise OAuthFlowError("oauth_token_exchange_failed", "OAuth token exchange failed", status=status)
        return self._token_material(_json_body(status, raw))

    def _complete_device(self, flow: Mapping[str, Any]) -> dict[str, Any]:
        if not self.spec.device_token_url:
            raise OAuthFlowError("flow_unavailable", "Device token endpoint is not established")
        interval = max(1.0, float(flow.get("interval") or 5))
        deadline = time.time() + float(flow.get("expires_in") or 600)
        while time.time() < deadline:
            status, _headers, raw = self._request(
                self.spec.device_token_url,
                method="POST",
                headers={"Content-Type": "application/json"},
                body=json.dumps({"device_auth_id": flow.get("device_auth_id"), "user_code": flow.get("user_code")}).encode(),
            )
            if status in {403, 404}:
                time.sleep(min(interval, max(0.0, deadline - time.time())))
                continue
            if status < 200 or status >= 300:
                raise OAuthFlowError("oauth_device_poll_failed", "Device token polling failed", status=status)
            data = _json_body(status, raw)
            auth_code, verifier = data.get("authorization_code"), data.get("code_verifier")
            if not isinstance(auth_code, str) or not isinstance(verifier, str):
                raise OAuthFlowError("oauth_invalid_response", "Device response missing authorization_code or code_verifier")
            return self._exchange_device_code(auth_code, verifier)
        raise OAuthFlowError("oauth_device_timeout", "Device authorization timed out")

    def _exchange_device_code(self, code: str, verifier: str) -> dict[str, Any]:
        values = {"grant_type": "authorization_code", "client_id": self._require_client_id(), "code": code, "code_verifier": verifier, "redirect_uri": self.spec.device_redirect_uri or ""}
        status, _headers, raw = self._request(self.spec.token_url, method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"}, body=urllib.parse.urlencode(values).encode())
        if status < 200 or status >= 300:
            raise OAuthFlowError("oauth_token_exchange_failed", "OAuth token exchange failed", status=status)
        return self._token_material(_json_body(status, raw))

    @staticmethod
    def _token_material(data: Mapping[str, Any], *, require_refresh: bool = True) -> dict[str, Any]:
        access, refresh, expires = data.get("access_token"), data.get("refresh_token"), data.get("expires_in")
        if not isinstance(access, str) or not isinstance(expires, (int, float)) or (require_refresh and not isinstance(refresh, str)):
            raise OAuthFlowError("oauth_invalid_response", "Token response missing access_token, refresh_token, or expires_in")
        result: dict[str, Any] = {"access_token": access, "token_type": data.get("token_type", "Bearer"), "expires_at_ms": int(time.time() * 1000 + float(expires) * 1000)}
        if isinstance(refresh, str) and refresh:
            result["refresh_token"] = refresh
        account_id = _jwt_claim(access, ("https://api.openai.com/auth", "chatgpt_account_id"))
        email = _jwt_claim(access, ("https://api.openai.com/profile", "email"))
        if account_id: result["provider_account_id"] = account_id
        if email: result["email"] = email
        for key in ("projectId", "project_id"):
            if isinstance(data.get(key), str): result["project_id"] = data[key]
        return result

    def refresh(self, material: Mapping[str, Any]) -> dict[str, Any]:
        refresh_token = material.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise OAuthFlowError("oauth_refresh_unavailable", "Stored OAuth credential has no refresh token")
        values = {"grant_type": "refresh_token", "client_id": self._require_client_id(), "refresh_token": refresh_token}
        headers = {"Content-Type": "application/json"} if self.spec.flow_id == "anthropic" else {"Content-Type": "application/x-www-form-urlencoded"}
        if self.spec.flow_id == "anthropic":
            headers.update({"anthropic-beta": "oauth-2025-04-20", "User-Agent": "anthropic-sdk-typescript/0.94.0 userOAuthProvider"})
        body = json.dumps(values).encode() if self.spec.flow_id == "anthropic" else urllib.parse.urlencode(values).encode()
        status, _response_headers, raw = self._request(self.spec.token_url, method="POST", headers=headers, body=body)
        if status < 200 or status >= 300:
            raise OAuthFlowError("oauth_refresh_failed", "OAuth token refresh failed", status=status)
        result = self._token_material(_json_body(status, raw), require_refresh=False)
        if not result.get("refresh_token"):
            result["refresh_token"] = refresh_token
        return result
