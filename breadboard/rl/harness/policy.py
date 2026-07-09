from __future__ import annotations

import asyncio
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping


class PolicyRouteError(ValueError):
    pass


class PolicyResponseError(RuntimeError):
    pass


class _NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _allowed_policy_hosts() -> set[str]:
    raw = os.environ.get("BREADBOARD_POLICY_ALLOWED_HOSTS", "127.0.0.1,localhost,::1")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def validate_policy_base_url(base_url: str) -> str:
    parsed = urllib.parse.urlsplit(base_url.strip())
    if parsed.scheme not in {"http", "https"}:
        raise PolicyRouteError("policy base_url must use http or https")
    if not parsed.hostname or parsed.username or parsed.password:
        raise PolicyRouteError("policy base_url must contain a host and no userinfo")
    if parsed.query or parsed.fragment or parsed.path not in {"", "/"}:
        raise PolicyRouteError(
            "policy base_url cannot contain a path, query, or fragment"
        )
    hostname = parsed.hostname.lower()
    netloc = parsed.netloc.lower()
    allowed = _allowed_policy_hosts()
    if hostname not in allowed and netloc not in allowed:
        raise PolicyRouteError(
            f"policy host {hostname!r} is not in BREADBOARD_POLICY_ALLOWED_HOSTS"
        )
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, "", "", "")).rstrip(
        "/"
    )


class PolicyClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout_seconds: int = 300,
        max_response_bytes: int = 64 * 1024 * 1024,
    ):
        self.base_url = validate_policy_base_url(base_url)
        self.timeout_seconds = int(timeout_seconds)
        self.max_response_bytes = int(max_response_bytes)
        if self.timeout_seconds < 1 or self.max_response_bytes < 1:
            raise ValueError("policy timeout and response byte limit must be positive")
        self._opener = urllib.request.build_opener(_NoRedirects())

    async def generate(self, request_payload: Mapping[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self._generate_sync, dict(request_payload))

    def _generate_sync(self, request_payload: dict[str, Any]) -> dict[str, Any]:
        request = urllib.request.Request(
            f"{self.base_url}/v1/responses",
            data=json.dumps(request_payload, separators=(",", ":")).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                content_type = response.headers.get_content_type()
                if content_type != "application/json":
                    raise PolicyResponseError(
                        f"policy response content type must be application/json, got {content_type!r}"
                    )
                raw = response.read(self.max_response_bytes + 1)
        except urllib.error.HTTPError as exc:
            body = exc.read(4096).decode("utf-8", errors="replace")
            raise PolicyResponseError(
                f"policy endpoint returned HTTP {exc.code}: {body}"
            ) from exc
        except urllib.error.URLError as exc:
            raise PolicyResponseError(
                f"policy endpoint request failed: {exc.reason}"
            ) from exc
        if len(raw) > self.max_response_bytes:
            raise PolicyResponseError(
                "policy response exceeded the configured byte limit"
            )
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PolicyResponseError("policy endpoint returned invalid JSON") from exc
        if not isinstance(payload, dict) or not isinstance(payload.get("output"), list):
            raise PolicyResponseError(
                "policy response must be an object with an output list"
            )
        return payload


__all__ = [
    "PolicyClient",
    "PolicyResponseError",
    "PolicyRouteError",
    "validate_policy_base_url",
]
