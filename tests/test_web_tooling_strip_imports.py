from __future__ import annotations

import pytest
import socket


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
    from breadboard_engine.web.providers import (
        ProviderError,
        SerperClient,
        SerperSearchRequest,
    )

    client = SerperClient(api_key=None)
    with pytest.raises(ProviderError):
        await client.search(SerperSearchRequest(q="test"))


@pytest.mark.asyncio
async def test_scrapedo_fails_fast_without_key() -> None:
    from breadboard_engine.web.providers import (
        ProviderError,
        ScrapeDoClient,
        ScrapeDoRequest,
    )

    client = ScrapeDoClient(api_key=None)
    with pytest.raises(ProviderError):
        await client.fetch(ScrapeDoRequest(url="https://example.com"))


def test_scraper_rejects_loopback_and_private_dns_targets(monkeypatch) -> None:
    from breadboard_engine.web import scraper as scraper_module

    def _resolve(host: str, port: int, **_kwargs):
        addresses = {
            "public.example": "93.184.216.34",
            "private.example": "10.23.45.67",
        }
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (addresses[host], port),
            )
        ]

    monkeypatch.setattr(scraper_module.socket, "getaddrinfo", _resolve)

    assert (
        scraper_module._validate_http_url("https://public.example/path")
        == "https://public.example/path"
    )
    for unsafe in (
        "http://127.0.0.1:8077/v1/auth/credentials",
        "http://[::1]/v1/auth/login-sessions",
        "http://localhost/v1/auth/credentials",
        "https://private.example/resource",
    ):
        with pytest.raises(scraper_module._InvalidURLScheme):
            scraper_module._validate_http_url(unsafe)


@pytest.mark.asyncio
async def test_scraper_validates_redirect_before_following_private_target(
    monkeypatch,
) -> None:
    from breadboard_engine.web import scraper as scraper_module

    requested: list[str] = []

    def _resolve(host: str, port: int, **_kwargs):
        assert host == "public.example"
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    scraper = scraper_module.WebScraper()

    def _request_once(
        method,
        destination,
        *,
        headers,
        timeout_s,
        verify_tls,
    ):
        del method, headers, timeout_s, verify_tls
        requested.append(destination.url)
        return (
            302,
            {"location": ("http://127.0.0.1:8077/v1/auth/credentials")},
            b"",
        )

    monkeypatch.setattr(scraper_module.socket, "getaddrinfo", _resolve)
    monkeypatch.setattr(scraper, "_request_once", _request_once)
    with pytest.raises(scraper_module._InvalidURLScheme):
        await scraper._fetch_bytes(
            "https://public.example/start",
            headers={},
            timeout_s=1.0,
            verify_tls=True,
        )

    assert requested == ["https://public.example/start"]


@pytest.mark.asyncio
async def test_scraper_pins_the_validated_dns_result(
    monkeypatch,
) -> None:
    from breadboard_engine.web import scraper as scraper_module

    resolution_count = 0
    observed_addresses = []

    def _resolve(_host: str, port: int, **_kwargs):
        nonlocal resolution_count
        resolution_count += 1
        address = "93.184.216.34" if resolution_count == 1 else "127.0.0.1"
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                (address, port),
            )
        ]

    scraper = scraper_module.WebScraper()

    def _request_once(
        method,
        destination,
        *,
        headers,
        timeout_s,
        verify_tls,
    ):
        del method, headers, timeout_s, verify_tls
        observed_addresses.extend(destination.addresses)
        return 200, {"content-type": "text/plain"}, b"ok"

    monkeypatch.setattr(scraper_module.socket, "getaddrinfo", _resolve)
    monkeypatch.setattr(scraper, "_request_once", _request_once)

    status, _headers, body = await scraper._fetch_bytes(
        "https://public.example/resource",
        headers={},
        timeout_s=1.0,
        verify_tls=True,
    )

    assert status == 200
    assert body == b"ok"
    assert resolution_count == 1
    assert observed_addresses[0][3][0] == "93.184.216.34"


@pytest.mark.asyncio
async def test_browser_launch_keeps_chromium_sandbox_and_disables_direct_network(
    monkeypatch,
) -> None:
    import sys
    from types import ModuleType, SimpleNamespace

    from breadboard_engine.web import scraper as scraper_module

    launch_options = {}
    fake_browser = SimpleNamespace()

    async def _launch(**kwargs):
        launch_options.update(kwargs)
        return fake_browser

    fake_playwright = SimpleNamespace(chromium=SimpleNamespace(launch=_launch))

    async def _start():
        return fake_playwright

    async_api = ModuleType("playwright.async_api")
    async_api.async_playwright = lambda: SimpleNamespace(start=_start)
    playwright = ModuleType("playwright")
    playwright.async_api = async_api
    monkeypatch.setitem(sys.modules, "playwright", playwright)
    monkeypatch.setitem(sys.modules, "playwright.async_api", async_api)

    scraper = scraper_module.WebScraper()
    assert await scraper._ensure_browser() is fake_browser

    arguments = launch_options["args"]
    assert "--no-sandbox" not in arguments
    assert "--disable-quic" in arguments
    assert "--force-webrtc-ip-handling-policy=disable_non_proxied_udp" in arguments
    assert "--host-resolver-rules=MAP * ~NOTFOUND" in arguments
    assert "--proxy-bypass-list=<-loopback>" in arguments


@pytest.mark.asyncio
async def test_crawler_metadata_fetches_use_pinned_scraper_transport(
    monkeypatch,
) -> None:
    from breadboard_engine.web import crawler as crawler_module
    from breadboard_engine.web import scraper as scraper_module

    scraper = scraper_module.WebScraper()
    requests = []

    async def _fetch_bytes(url, **kwargs):
        requests.append((url, kwargs))
        return (
            200,
            {"content-type": "application/xml"},
            b"<urlset><url><loc>https://public.example/a</loc></url></urlset>",
        )

    monkeypatch.setattr(scraper, "_fetch_bytes", _fetch_bytes)

    links = await crawler_module._get_sitemap_links(
        "https://public.example/start",
        scraper=scraper,
    )

    assert links == ["https://public.example/a"]
    assert requests[0][0] == "https://public.example/sitemap.xml"
    assert requests[0][1]["verify_tls"] is True
