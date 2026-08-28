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
async def test_scraper_drops_caller_headers_after_cross_origin_redirect(
    monkeypatch,
) -> None:
    from breadboard_engine.web import scraper as scraper_module

    def _resolve(_host: str, port: int, **_kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    observed: list[tuple[str, dict[str, str]]] = []
    scraper = scraper_module.WebScraper()

    def _request_once(
        method,
        destination,
        *,
        headers,
        timeout_s,
        verify_tls,
    ):
        del method, timeout_s, verify_tls
        observed.append((destination.url, dict(headers)))
        if len(observed) == 1:
            return 302, {"location": "https://redirect.example/final"}, b""
        return 200, {"content-type": "text/plain"}, b"ok"

    monkeypatch.setattr(scraper_module.socket, "getaddrinfo", _resolve)
    monkeypatch.setattr(scraper, "_request_once", _request_once)

    await scraper._fetch_bytes(
        "https://public.example/start",
        headers={"Authorization": "Bearer origin-secret", "X-Api-Key": "origin-secret"},
        timeout_s=1.0,
        verify_tls=True,
    )

    assert observed == [
        (
            "https://public.example/start",
            {"Authorization": "Bearer origin-secret", "X-Api-Key": "origin-secret"},
        ),
        ("https://redirect.example/final", {}),
    ]


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
async def test_browser_scopes_caller_headers_and_ignores_blocked_post_subrequest(
    monkeypatch,
) -> None:
    from breadboard_engine.web import scraper as scraper_module

    def _resolve(_host: str, port: int, **_kwargs):
        return [
            (
                socket.AF_INET,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                "",
                ("93.184.216.34", port),
            )
        ]

    class Request:
        def __init__(self, method: str, url: str, headers: dict[str, str]) -> None:
            self.method = method
            self.url = url
            self.headers = headers

        async def all_headers(self) -> dict[str, str]:
            return dict(self.headers)

    class Route:
        def __init__(self, request: Request) -> None:
            self.request = request
            self.aborted: str | None = None

        async def abort(self, reason: str) -> None:
            self.aborted = reason

        async def fulfill(self, **_response) -> None:
            return None

    class Page:
        url = "https://public.example/start"

        def __init__(self) -> None:
            self.route_handler = None
            self.post_route: Route | None = None

        async def route(self, _pattern: str, handler) -> None:
            self.route_handler = handler

        async def goto(self, _url: str, **_kwargs):
            assert self.route_handler is not None
            for request in (
                Request("GET", "https://public.example/start", {"accept": "text/html"}),
                Request("GET", "https://cdn.example/app.js", {"accept": "*/*"}),
            ):
                await self.route_handler(Route(request))
            self.post_route = Route(
                Request("POST", "https://public.example/analytics", {})
            )
            await self.route_handler(self.post_route)
            return type("Response", (), {"status": 200})()

        async def content(self) -> str:
            return "<html></html>"

        async def close(self) -> None:
            return None

    class Context:
        def __init__(self, page: Page) -> None:
            self.page = page

        async def add_init_script(self, _script: str) -> None:
            return None

        async def new_page(self) -> Page:
            return self.page

        async def close(self) -> None:
            return None

    class Browser:
        def __init__(self, context: Context) -> None:
            self.context = context

        async def new_context(self, **_kwargs) -> Context:
            return self.context

    observed: list[tuple[str, dict[str, str]]] = []
    page = Page()
    scraper = scraper_module.WebScraper()

    async def _ensure_browser():
        return Browser(Context(page))

    def _request_once(
        method,
        destination,
        *,
        headers,
        timeout_s,
        verify_tls,
    ):
        del method, timeout_s, verify_tls
        observed.append((destination.url, dict(headers)))
        return 200, {"content-type": "text/plain"}, b"ok"

    async def _process_html(*_args, **_kwargs):
        return "processed"

    monkeypatch.setattr(scraper_module.socket, "getaddrinfo", _resolve)
    monkeypatch.setattr(scraper, "_ensure_browser", _ensure_browser)
    monkeypatch.setattr(scraper, "_request_once", _request_once)
    monkeypatch.setattr(scraper, "_process_html", _process_html)
    options = scraper_module.ScrapeOptions(
        headers={"Authorization": "Bearer origin-secret", "X-Api-Key": "origin-secret"},
        render_js=True,
    )

    result = await scraper._scrape_with_browser(
        "https://public.example/start",
        headers=dict(options.headers or {}),
        options=options,
        timeout_s=1.0,
    )

    assert result == "processed"
    assert observed[0][1]["Authorization"] == "Bearer origin-secret"
    assert observed[0][1]["X-Api-Key"] == "origin-secret"
    assert observed[1] == ("https://cdn.example/app.js", {"accept": "*/*"})
    assert page.post_route is not None
    assert page.post_route.aborted == "blockedbyclient"


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
