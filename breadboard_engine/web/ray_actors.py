from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Any, Dict, Optional

import ray

from .crawler import CrawlRequest, crawl_site
from .frontier import MemoryFrontier, RedisFrontier, RedisFrontierConfig
from .models import ScrapeOptions
from .scraper import WebScraper, WebScraperSettings


def _stable_cache_key(prefix: str, payload: Dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"{prefix}:{digest}"


@ray.remote
class WebScraperActor:
    """Long-lived scraper actor (reuses Playwright/browser state when enabled)."""

    def __init__(
        self,
        *,
        settings: Optional[Dict[str, Any]] = None,
        redis_url: Optional[str] = None,
        cache_ttl_s: int = 3600,
        cache_namespace: str = "kc_web_cache",
    ):
        self.scraper = WebScraper(settings=WebScraperSettings(**(settings or {})))
        self.cache_ttl_s = int(cache_ttl_s)
        self.cache_namespace = cache_namespace.strip(":") or "kc_web_cache"
        self._redis = None
        if redis_url:
            try:
                import redis  # type: ignore

                self._redis = redis.from_url(redis_url, decode_responses=True)
            except Exception:
                self._redis = None

    async def close(self) -> None:
        await self.scraper.close()

    async def scrape_url(self, url: str, *, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        opts = ScrapeOptions(**(options or {}))
        cache_key = None
        if self._redis is not None:
            cache_key = _stable_cache_key(
                self.cache_namespace,
                {"tool": "scrape_url", "url": url, "options": asdict(opts)},
            )
            try:
                cached = self._redis.get(cache_key)
                if cached:
                    return json.loads(cached)
            except Exception:
                cache_key = None

        doc = await self.scraper.scrape_url(url, options=opts)
        payload = asdict(doc)

        if self._redis is not None and cache_key is not None:
            try:
                self._redis.set(cache_key, json.dumps(payload, ensure_ascii=False), ex=max(1, self.cache_ttl_s))
            except Exception:
                pass

        return payload


@ray.remote
class CrawlCoordinatorActor:
    """Hermes-style crawl coordinator (single-actor MVI; scalable via worker pools later)."""

    def __init__(
        self,
        *,
        redis_frontier_url: Optional[str] = None,
        frontier_namespace: str = "kc_crawl_frontier",
        scraper_settings: Optional[Dict[str, Any]] = None,
    ):
        self.scraper = WebScraper(settings=WebScraperSettings(**(scraper_settings or {})))
        self.redis_frontier_url = redis_frontier_url
        self.frontier_namespace = frontier_namespace.strip(":") or "kc_crawl_frontier"

    async def close(self) -> None:
        await self.scraper.close()

    async def crawl(self, req: Dict[str, Any]) -> Dict[str, Any]:
        crawl_req = CrawlRequest(**req)
        frontier = None
        if self.redis_frontier_url:
            frontier = RedisFrontier(
                RedisFrontierConfig(redis_url=self.redis_frontier_url, namespace=self.frontier_namespace)
            )
        else:
            frontier = MemoryFrontier()
        docs = await crawl_site(crawl_req, frontier=frontier, scraper=self.scraper)
        return {"success": True, "count": len(docs), "data": [asdict(doc) for doc in docs]}

