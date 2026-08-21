from __future__ import annotations

import pytest


def test_web_tooling_imports_and_basic_construction() -> None:
    from breadboard_engine.web import ScrapeOptions, WebScraper
    from breadboard_engine.web.crawler import CrawlRequest
    from breadboard_engine.web.frontier import MemoryFrontier, RedisFrontierConfig
    from breadboard_engine.web.providers import ScrapeDoClient, SerperClient

    _ = ScrapeOptions()
    _ = CrawlRequest(url="https://example.com")
    _ = WebScraper()
    _ = MemoryFrontier()
    _ = RedisFrontierConfig(redis_url="redis://localhost:6379/0")
    _ = SerperClient(api_key="test")
    _ = ScrapeDoClient(api_key="test")

@pytest.mark.asyncio
async def test_serper_fails_fast_without_key() -> None:
    from breadboard_engine.web.providers import ProviderError, SerperClient, SerperSearchRequest

    client = SerperClient(api_key=None)
    with pytest.raises(ProviderError):
        await client.search(SerperSearchRequest(q="test"))


@pytest.mark.asyncio
async def test_scrapedo_fails_fast_without_key() -> None:
    from breadboard_engine.web.providers import ProviderError, ScrapeDoClient, ScrapeDoRequest

    client = ScrapeDoClient(api_key=None)
    with pytest.raises(ProviderError):
        await client.fetch(ScrapeDoRequest(url="https://example.com"))

