from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import List, Optional, Sequence
from urllib.parse import urljoin, urlparse, urldefrag
from urllib.robotparser import RobotFileParser

import httpx

from .frontier import BaseFrontier, MemoryFrontier
from .models import ScrapeOptions, WebDocument
from .scraper import WebScraper


@dataclass(frozen=True)
class CrawlRequest:
    url: str
    max_depth: int = 2
    limit: int = 50
    include_paths: Optional[List[str]] = None
    exclude_paths: Optional[List[str]] = None
    allow_external_links: bool = False
    include_subdomains: bool = False
    ignore_sitemap: bool = False
    delay_s: float = 0.0
    max_time_s: Optional[float] = None
    max_bytes: Optional[int] = None
    max_concurrency: int = 10
    scrape_options: Optional[ScrapeOptions] = None


async def _get_sitemap_links(url: str) -> List[str]:
    parsed = urlparse(url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    sitemap_url = urljoin(origin, "/sitemap.xml")
    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp = await client.get(sitemap_url)
            if resp.status_code != 200:
                return []
            body = resp.text
    except Exception:
        return []
    # Minimal XML parse: look for <loc>...</loc>
    links: List[str] = []
    start = 0
    while True:
        idx = body.find("<loc>", start)
        if idx == -1:
            break
        end = body.find("</loc>", idx)
        if end == -1:
            break
        loc = body[idx + 5 : end].strip()
        if loc:
            links.append(loc)
        start = end + 6
    return links


def _normalize_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    url, _frag = urldefrag(url)
    return url


async def crawl_site(
    req: CrawlRequest,
    *,
    frontier: Optional[BaseFrontier] = None,
    scraper: Optional[WebScraper] = None,
) -> List[WebDocument]:
    req = CrawlRequest(**{**req.__dict__, "url": _normalize_url(req.url)})
    if not req.url.startswith(("http://", "https://")):
        raise ValueError("crawl url must start with http:// or https://")

    frontier = frontier or MemoryFrontier()
    created_scraper = scraper is None
    scraper = scraper or WebScraper()
    options = req.scrape_options or ScrapeOptions(formats=["markdown", "links"])

    start_parsed = urlparse(req.url)
    base_domain = start_parsed.netloc

    robots_cache: dict[str, RobotFileParser] = {}
    host_semaphores: dict[str, asyncio.Semaphore] = {}
    host_locks: dict[str, asyncio.Lock] = {}
    host_last_fetch: dict[str, float] = {}

    async def get_robots(u: str) -> RobotFileParser:
        parsed = urlparse(u)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in robots_cache:
            return robots_cache[origin]
        rp = RobotFileParser()
        robots_url = urljoin(origin, "/robots.txt")
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
                resp = await client.get(robots_url)
                if resp.status_code == 200:
                    rp.parse(resp.text.splitlines())
                else:
                    rp.parse([])
        except Exception:
            rp.parse([])
        robots_cache[origin] = rp
        return rp

    def domain_allowed(u: str) -> bool:
        parsed = urlparse(u)
        if req.allow_external_links:
            return True
        if req.include_subdomains:
            return parsed.netloc == base_domain or parsed.netloc.endswith("." + base_domain)
        return parsed.netloc == base_domain

    def path_allowed(u: str) -> bool:
        parsed = urlparse(u)
        path = parsed.path or "/"
        if req.include_paths and not any(path.startswith(p) for p in (req.include_paths or [])):
            return False
        if req.exclude_paths and any(path.startswith(p) for p in (req.exclude_paths or [])):
            return False
        return True

    async def enqueue(u: str, depth: int) -> None:
        u = _normalize_url(u)
        if not u.startswith(("http://", "https://")):
            return
        if depth > req.max_depth:
            return
        if not domain_allowed(u) or not path_allowed(u):
            return
        if req.limit and (len(results) + frontier.size()) >= req.limit:
            return
        if frontier.seen(u):
            return
        await frontier.enqueue(u, depth)

    # Seed
    results: List[WebDocument] = []
    await enqueue(req.url, 0)

    # Optional sitemap boost
    if not req.ignore_sitemap:
        try:
            for link in await _get_sitemap_links(req.url):
                if req.limit and (len(results) + frontier.size()) >= req.limit:
                    break
                await enqueue(link, 0)
        except Exception:
            pass

    start_time = time.time()
    total_bytes = 0

    concurrency = max(1, min(int(req.max_concurrency), 64))

    async def worker(worker_id: int) -> None:
        nonlocal total_bytes
        while True:
            if req.max_time_s is not None and (time.time() - start_time) > float(req.max_time_s):
                break
            if req.max_bytes is not None and total_bytes >= int(req.max_bytes):
                break
            if req.limit and len(results) >= req.limit:
                break
            try:
                url, depth = await frontier.dequeue(timeout=0.5)
            except asyncio.TimeoutError:
                if frontier.size() == 0 and frontier.inflight_size() == 0:
                    break
                continue
            try:
                if req.limit and len(results) >= req.limit:
                    continue
                rp = await get_robots(url)
                try:
                    if not rp.can_fetch("*", url):
                        results.append(WebDocument(url=url))
                        continue
                except Exception:
                    pass

                parsed = urlparse(url)
                host = parsed.netloc
                sem = host_semaphores.setdefault(host, asyncio.Semaphore(1))
                lock = host_locks.setdefault(host, asyncio.Lock())
                async with sem:
                    # politeness delay per host
                    async with lock:
                        last = host_last_fetch.get(host, 0.0)
                        if last and req.delay_s and req.delay_s > 0:
                            wait = max(0.0, float(req.delay_s) - (time.time() - last))
                            if wait > 0:
                                await asyncio.sleep(wait)
                        host_last_fetch[host] = time.time()

                    doc = await scraper.scrape_url(url, options=options)
                    results.append(doc)
                    # crude byte accounting: markdown+html+text
                    total_bytes += len((doc.markdown or "") + (doc.html or "") + (doc.text or ""))

                    if depth < req.max_depth and doc.links:
                        for link in doc.links:
                            if req.limit and len(results) >= req.limit:
                                break
                            await enqueue(link, depth + 1)
            finally:
                try:
                    await frontier.mark_done(url)
                except Exception:
                    pass

    tasks = [asyncio.create_task(worker(i)) for i in range(concurrency)]
    try:
        await asyncio.gather(*tasks)
    finally:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if created_scraper:
            try:
                await scraper.close()
            except Exception:
                pass

    return results


def crawl_site_sync(req: CrawlRequest, *, scraper: Optional[WebScraper] = None) -> List[WebDocument]:
    return asyncio.run(crawl_site(req, scraper=scraper))

