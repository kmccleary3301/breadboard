from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_base_ms: int = 200
    backoff_max_ms: int = 3000


class ResilientHttpClient:
    """Async HTTP client with retries + exponential backoff (Hermes-derived)."""

    def __init__(
        self,
        *,
        timeout_s: float = 30.0,
        follow_redirects: bool = True,
        retry: Optional[RetryPolicy] = None,
        verify_tls: bool = True,
    ):
        self.timeout_s = float(timeout_s)
        self.follow_redirects = bool(follow_redirects)
        self.retry = retry or RetryPolicy()
        self.verify_tls = bool(verify_tls)

    def _compute_backoff_s(self, attempt: int) -> float:
        base = max(0.0, float(self.retry.backoff_base_ms) / 1000.0)
        cap = max(base, float(self.retry.backoff_max_ms) / 1000.0)
        exp = min(cap, base * (2 ** max(0, attempt - 1)))
        jitter = random.uniform(0, exp / 2) if exp > 0 else 0.0
        return exp + jitter

    def _should_retry(self, exc: Exception, status_code: Optional[int]) -> bool:
        if status_code in (429, 500, 502, 503, 504):
            return True
        try:
            import httpx  # type: ignore

            return isinstance(exc, (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteError, httpx.PoolTimeout))
        except Exception:
            return False

    async def get(self, url: str, *, headers: Optional[dict] = None):
        try:
            import httpx  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"ResilientHttpClient requires httpx: {exc}") from exc

        attempts = max(1, int(self.retry.max_attempts))
        async with httpx.AsyncClient(
            timeout=self.timeout_s,
            follow_redirects=self.follow_redirects,
            verify=self.verify_tls,
        ) as client:
            for attempt in range(1, attempts + 1):
                resp = None
                try:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code in (429, 500, 502, 503, 504):
                        ra = resp.headers.get("Retry-After")
                        if ra:
                            try:
                                await asyncio.sleep(float(ra))
                            except Exception:
                                pass
                        raise httpx.HTTPStatusError("retryable status", request=resp.request, response=resp)
                    return resp
                except Exception as exc:
                    status_code = getattr(resp, "status_code", None)
                    if attempt >= attempts or not self._should_retry(exc, status_code):
                        raise
                    await asyncio.sleep(self._compute_backoff_s(attempt))

    async def head(self, url: str, *, headers: Optional[dict] = None):
        try:
            import httpx  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"ResilientHttpClient requires httpx: {exc}") from exc

        attempts = max(1, int(self.retry.max_attempts))
        async with httpx.AsyncClient(
            timeout=self.timeout_s,
            follow_redirects=self.follow_redirects,
            verify=self.verify_tls,
        ) as client:
            for attempt in range(1, attempts + 1):
                resp = None
                try:
                    resp = await client.head(url, headers=headers)
                    if resp.status_code in (429, 500, 502, 503, 504):
                        ra = resp.headers.get("Retry-After")
                        if ra:
                            try:
                                await asyncio.sleep(float(ra))
                            except Exception:
                                pass
                        raise httpx.HTTPStatusError("retryable status", request=resp.request, response=resp)
                    return resp
                except Exception as exc:
                    status_code = getattr(resp, "status_code", None)
                    if attempt >= attempts or not self._should_retry(exc, status_code):
                        raise
                    await asyncio.sleep(self._compute_backoff_s(attempt))

