"""Hermes-derived web tooling (scrape/crawl/providers).

These modules intentionally keep the public interfaces small and optional so we
can:
- run locally inside the agent engine (Python-only path), and
- later swap to QueryLake/Hermes remote services with the same call surface.
"""

from .models import (
    DocumentMetadata,
    ScrapeOptions,
    WebDocument,
)
from .scraper import WebScraper
from .crawler import crawl_site, crawl_site_sync

__all__ = [
    "DocumentMetadata",
    "ScrapeOptions",
    "WebDocument",
    "WebScraper",
    "crawl_site",
    "crawl_site_sync",
]

