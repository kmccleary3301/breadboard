from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import httpx

from .base import ProviderError


@dataclass(frozen=True)
class SerperOrganicResult:
    title: Optional[str] = None
    link: Optional[str] = None
    snippet: Optional[str] = None
    date: Optional[str] = None


@dataclass(frozen=True)
class SerperSearchRequest:
    q: str
    gl: Optional[str] = None
    hl: Optional[str] = None
    num: int = 10
    tbs: Optional[str] = None

    def to_payload(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"q": self.q, "num": int(self.num)}
        if self.gl:
            payload["gl"] = self.gl
        if self.hl:
            payload["hl"] = self.hl
        if self.tbs:
            payload["tbs"] = self.tbs
        return payload


@dataclass(frozen=True)
class SerperSearchResponse:
    organic: List[SerperOrganicResult] = field(default_factory=list)
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_json(cls, js: Any) -> "SerperSearchResponse":
        if not isinstance(js, dict):
            return cls()
        organic_raw = js.get("organic")
        organic: List[SerperOrganicResult] = []
        if isinstance(organic_raw, list):
            for item in organic_raw:
                if not isinstance(item, dict):
                    continue
                organic.append(
                    SerperOrganicResult(
                        title=item.get("title"),
                        link=item.get("link"),
                        snippet=item.get("snippet"),
                        date=item.get("date"),
                    )
                )
        return cls(organic=organic, raw=dict(js))


class SerperClient:
    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_s: float = 30.0,
    ):
        self.api_key = api_key or os.environ.get("SERPER_DEV_API_KEY") or os.environ.get("SERPER_API_KEY")
        self.base_url = (base_url or os.environ.get("SERPER_BASE_URL") or "https://google.serper.dev").rstrip("/")
        self.timeout_s = float(timeout_s)

    async def search(self, req: SerperSearchRequest) -> Tuple[SerperSearchResponse, float]:
        if not self.api_key:
            raise ProviderError("Serper.dev API key not configured")

        url = f"{self.base_url}/search"
        headers = {"X-API-KEY": self.api_key, "Content-Type": "application/json"}
        payload = req.to_payload()

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code >= 400:
            raise ProviderError("Serper.dev error", status_code=resp.status_code, payload=resp.text)
        try:
            js = resp.json()
        except Exception:
            js = {}
        cost = float(os.environ.get("KC_COST_SERPER_PER_REQUEST_USD") or os.environ.get("COST_SERPER_PER_REQUEST_USD") or 0.001)
        return SerperSearchResponse.from_json(js), cost

