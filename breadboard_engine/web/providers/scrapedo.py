from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional, Tuple
from urllib.parse import quote

import httpx

from .base import ProviderError


@dataclass(frozen=True)
class ScrapeDoRequest:
    url: str
    render_js: bool = False
    timeout_ms: int = 60000
    geo_code: Optional[str] = None


@dataclass(frozen=True)
class ScrapeDoResponse:
    content: str
    status_code: int
    content_type: Optional[str] = None


class ScrapeDoClient:
    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
    ):
        self.api_key = api_key or os.environ.get("SCRAPE_DO_API_KEY") or os.environ.get("SCRAPEDO_API_KEY")
        self.base_url = (base_url or os.environ.get("SCRAPEDO_BASE_URL") or "https://api.scrape.do").rstrip("/")

    async def fetch(self, req: ScrapeDoRequest) -> Tuple[ScrapeDoResponse, float]:
        if not self.api_key:
            raise ProviderError("Scrape.do API key not configured")

        timeout_ms = int(max(5000, min(int(req.timeout_ms or 60000), 120000)))
        params = [
            ("token", self.api_key),
            ("url", quote(str(req.url), safe="")),
        ]
        if req.render_js:
            params.append(("render", "true"))
        if timeout_ms:
            params.append(("timeout", str(timeout_ms)))
        if req.geo_code:
            params.append(("geoCode", req.geo_code))

        query = "&".join([f"{k}={v}" for k, v in params])
        url = f"{self.base_url}/?{query}"

        async with httpx.AsyncClient(timeout=timeout_ms / 1000.0, follow_redirects=True) as client:
            resp = await client.get(url)
        if resp.status_code >= 400:
            raise ProviderError("Scrape.do error", status_code=resp.status_code, payload=resp.text)
        cost = float(os.environ.get("KC_COST_SCRAPEDO_PER_REQUEST_USD") or os.environ.get("COST_SCRAPEDO_PER_REQUEST_USD") or 0.0001)
        return (
            ScrapeDoResponse(
                content=resp.text,
                status_code=int(resp.status_code),
                content_type=resp.headers.get("content-type"),
            ),
            cost,
        )

