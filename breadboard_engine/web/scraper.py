from __future__ import annotations

import asyncio
import base64
import http.client
import io
import ipaddress
import re
import socket
import ssl
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urljoin, urlsplit

from .models import DocumentMetadata, ScrapeOptions, WebDocument


class _InvalidURLScheme(ValueError):
    pass


@dataclass(frozen=True)
class _ResolvedHTTPDestination:
    url: str
    scheme: str
    hostname: str
    port: int
    request_target: str
    addresses: tuple[tuple[int, int, int, tuple[Any, ...]], ...]


def _resolve_http_destination(url: str) -> _ResolvedHTTPDestination:
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port or (443 if scheme == "https" else 80)
    except (TypeError, ValueError):
        raise _InvalidURLScheme(
            "Only public http and https URLs are supported"
        ) from None
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise _InvalidURLScheme("Only public http and https URLs are supported")
    normalized_host = hostname.rstrip(".").lower()
    if normalized_host == "localhost" or normalized_host.endswith(".localhost"):
        raise _InvalidURLScheme("Only public http and https URLs are supported")
    try:
        literal = ipaddress.ip_address(normalized_host.split("%", 1)[0])
    except ValueError:
        literal = None
    if literal is not None:
        if not literal.is_global:
            raise _InvalidURLScheme("Only public http and https URLs are supported")
        family = socket.AF_INET6 if literal.version == 6 else socket.AF_INET
        sockaddr: tuple[Any, ...] = (
            (str(literal), port, 0, 0) if literal.version == 6 else (str(literal), port)
        )
        addresses = (
            (
                family,
                socket.SOCK_STREAM,
                socket.IPPROTO_TCP,
                sockaddr,
            ),
        )
        peer_addresses = (literal,)
    else:
        try:
            resolved = socket.getaddrinfo(
                normalized_host,
                port,
                type=socket.SOCK_STREAM,
            )
            addresses = tuple(
                dict.fromkeys(
                    (
                        int(family),
                        int(socktype),
                        int(protocol),
                        tuple(resolved_sockaddr),
                    )
                    for (
                        family,
                        socktype,
                        protocol,
                        _name,
                        resolved_sockaddr,
                    ) in resolved
                    if resolved_sockaddr
                )
            )
            peer_addresses = tuple(
                ipaddress.ip_address(str(resolved_sockaddr[0]).split("%", 1)[0])
                for (
                    _family,
                    _socktype,
                    _protocol,
                    resolved_sockaddr,
                ) in addresses
            )
        except (OSError, UnicodeError, ValueError):
            raise _InvalidURLScheme("URL host cannot be resolved safely") from None
    if not peer_addresses or any(not address.is_global for address in peer_addresses):
        raise _InvalidURLScheme("Only public http and https URLs are supported")
    request_target = parsed.path or "/"
    if parsed.query:
        request_target = f"{request_target}?{parsed.query}"
    return _ResolvedHTTPDestination(
        url=url,
        scheme=scheme,
        hostname=normalized_host,
        port=port,
        request_target=request_target,
        addresses=addresses,
    )


def _validate_http_url(url: str) -> str:
    _resolve_http_destination(url)
    return url


_REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def _header_value(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    try:
        value = headers.get(name)
    except Exception:
        value = None
    if value is not None:
        return str(value)
    try:
        for key, candidate in headers.items():
            if str(key).lower() == name.lower():
                return str(candidate)
    except Exception:
        pass
    return None


def _validated_redirect(
    *,
    status_code: int,
    headers: Any,
    current_url: str,
) -> str | None:
    if int(status_code) not in _REDIRECT_STATUSES:
        return None
    location = _header_value(headers, "location")
    if not location:
        return None
    return _validate_http_url(urljoin(current_url, location))


@dataclass(frozen=True)
class WebScraperSettings:
    user_agent: str = "BreadBoardWeb/1.0"
    default_timeout_s: float = 30.0
    max_response_bytes: int = 5 * 1024 * 1024


class WebScraper:
    """Hermes-derived scraper with a stable, engine-friendly interface."""

    def __init__(self, *, settings: Optional[WebScraperSettings] = None):
        self.settings = settings or WebScraperSettings()
        self._browser = None

    async def close(self) -> None:
        browser = self._browser
        self._browser = None
        if browser is not None:
            try:
                await browser.close()
            except Exception:
                pass

    def _needs_browser_rendering(self, options: ScrapeOptions) -> bool:
        return bool(
            options.render_js or options.wait_for_ms is not None or options.mobile
        )

    async def _ensure_browser(self):
        if self._browser is not None:
            return self._browser
        try:
            from playwright.async_api import async_playwright  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                f"Playwright not available for JS rendering: {exc}"
            ) from exc

        pw = await async_playwright().start()
        self._browser = await pw.chromium.launch(
            headless=True,
            args=[
                "--disable-dev-shm-usage",
                "--disable-quic",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                "--host-resolver-rules=MAP * ~NOTFOUND",
                "--proxy-server=http://127.0.0.1:9",
                "--proxy-bypass-list=<-loopback>",
            ],
        )
        return self._browser

    async def scrape_url(
        self, url: str, *, options: Optional[ScrapeOptions] = None
    ) -> WebDocument:
        url = _validate_http_url(url)
        options = options or ScrapeOptions()
        headers: Dict[str, str] = {
            "User-Agent": self.settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            **(options.headers or {}),
        }

        timeout_s = (
            options.timeout_s
            if options.timeout_s is not None
            else self.settings.default_timeout_s
        )
        timeout_s = max(0.0, float(timeout_s))

        verify_tls = not bool(options.skip_tls_verification)

        # A pinned-address HEAD distinguishes HTML from file responses without
        # permitting the HTTP client to resolve the hostname a second time.
        content_type = ""
        try:
            head_status, head_headers = await self._fetch_headers(
                url,
                headers=headers,
                timeout_s=timeout_s,
                verify_tls=verify_tls,
            )
            redirect_location = next(
                (
                    value
                    for key, value in head_headers.items()
                    if str(key).lower() == "location"
                ),
                None,
            )
            if redirect_location:
                try:
                    _validate_http_url(urljoin(url, str(redirect_location)))
                except _InvalidURLScheme:
                    return WebDocument(
                        url=url,
                        metadata=DocumentMetadata(
                            source_url=url,
                            status_code=head_status,
                        ),
                    )
            if 200 <= head_status < 400:
                content_type = (head_headers.get("content-type") or "").lower()
            else:
                content_type = ""
        except _InvalidURLScheme:
            raise
        except Exception:
            content_type = ""

        try:
            if "text/html" in content_type or not content_type:
                if self._needs_browser_rendering(options):
                    return await self._scrape_with_browser(
                        url, headers=headers, options=options, timeout_s=timeout_s
                    )
                return await self._scrape_with_http(
                    url, headers=headers, options=options, timeout_s=timeout_s
                )
            return await self._scrape_file(
                url,
                headers=headers,
                options=options,
                timeout_s=timeout_s,
                content_type=content_type,
            )
        except _InvalidURLScheme:
            raise
        except Exception as exc:
            meta = DocumentMetadata(
                source_url=url,
                status_code=getattr(
                    getattr(exc, "response", None), "status_code", None
                ),
            )
            return WebDocument(url=url, metadata=meta)

    @staticmethod
    def _request_headers(headers: Dict[str, str]) -> Dict[str, str]:
        blocked = {
            "connection",
            "content-length",
            "host",
            "proxy-authorization",
            "proxy-connection",
            "transfer-encoding",
        }
        safe = {
            str(key): str(value)
            for key, value in headers.items()
            if str(key).lower() not in blocked
        }
        safe["Accept-Encoding"] = "identity"
        safe["Connection"] = "close"
        return safe

    @staticmethod
    def _connect_destination(
        destination: _ResolvedHTTPDestination,
        *,
        timeout_s: float,
        verify_tls: bool,
    ) -> socket.socket:
        last_error: OSError | None = None
        for family, socktype, protocol, sockaddr in destination.addresses:
            connection = socket.socket(family, socktype, protocol)
            try:
                connection.settimeout(max(0.001, timeout_s))
                connection.connect(sockaddr)
                peer = ipaddress.ip_address(
                    str(connection.getpeername()[0]).split("%", 1)[0]
                )
                if not peer.is_global:
                    raise _InvalidURLScheme("Connected peer is not a public address")
                if destination.scheme == "https":
                    if verify_tls:
                        context = ssl.create_default_context()
                    else:
                        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                        context.check_hostname = False
                        context.verify_mode = ssl.CERT_NONE
                    connection = context.wrap_socket(
                        connection,
                        server_hostname=destination.hostname,
                    )
                return connection
            except _InvalidURLScheme:
                connection.close()
                raise
            except OSError as exc:
                last_error = exc
                connection.close()
        if last_error is not None:
            raise last_error
        raise OSError("No public address was available")

    def _request_once(
        self,
        method: str,
        destination: _ResolvedHTTPDestination,
        *,
        headers: Dict[str, str],
        timeout_s: float,
        verify_tls: bool,
    ) -> tuple[int, Dict[str, str], bytes]:
        connection = http.client.HTTPConnection(
            destination.hostname,
            destination.port,
            timeout=max(0.001, timeout_s),
        )
        connection.sock = self._connect_destination(
            destination,
            timeout_s=timeout_s,
            verify_tls=verify_tls,
        )
        try:
            connection.request(
                method,
                destination.request_target,
                headers=self._request_headers(headers),
            )
            response = connection.getresponse()
            status_code = int(response.status)
            response_headers = {
                str(key).lower(): str(value) for key, value in response.getheaders()
            }
            content_length = response_headers.get("content-length")
            if content_length is not None:
                try:
                    declared_length = int(content_length)
                except ValueError:
                    declared_length = None
                if (
                    declared_length is not None
                    and declared_length > self.settings.max_response_bytes
                ):
                    raise ValueError("Response too large")
            if method == "HEAD" or status_code in _REDIRECT_STATUSES:
                body = b""
            else:
                body = response.read(self.settings.max_response_bytes + 1)
                if len(body) > self.settings.max_response_bytes:
                    raise ValueError("Response too large")
            return status_code, response_headers, body
        finally:
            connection.close()

    async def _fetch_headers(
        self,
        url: str,
        *,
        headers: Dict[str, str],
        timeout_s: float,
        verify_tls: bool,
    ) -> tuple[int, Dict[str, str]]:
        """Return response metadata through a DNS-pinned direct connection."""
        current_url = url
        for _redirect in range(11):
            destination = _resolve_http_destination(current_url)
            status_code, response_headers, _body = await asyncio.to_thread(
                self._request_once,
                "HEAD",
                destination,
                headers=headers,
                timeout_s=timeout_s,
                verify_tls=verify_tls,
            )
            redirect = _validated_redirect(
                status_code=status_code,
                headers=response_headers,
                current_url=current_url,
            )
            if redirect is None:
                return status_code, response_headers
            current_url = redirect
        raise _InvalidURLScheme("Too many redirects")

    async def _fetch_bytes(
        self,
        url: str,
        *,
        headers: Dict[str, str],
        timeout_s: float,
        verify_tls: bool,
    ) -> tuple[int, Dict[str, str], bytes]:
        """Return a bounded body through a DNS-pinned direct connection."""
        current_url = url
        for _redirect in range(11):
            destination = _resolve_http_destination(current_url)
            status_code, response_headers, body = await asyncio.to_thread(
                self._request_once,
                "GET",
                destination,
                headers=headers,
                timeout_s=timeout_s,
                verify_tls=verify_tls,
            )
            redirect = _validated_redirect(
                status_code=status_code,
                headers=response_headers,
                current_url=current_url,
            )
            if redirect is None:
                return status_code, response_headers, body
            current_url = redirect
        raise _InvalidURLScheme("Too many redirects")

    async def _scrape_with_http(
        self,
        url: str,
        *,
        headers: Dict[str, str],
        options: ScrapeOptions,
        timeout_s: float,
    ) -> WebDocument:
        status_code, resp_headers, body = await self._fetch_bytes(
            url,
            headers=headers,
            timeout_s=timeout_s,
            verify_tls=not options.skip_tls_verification,
        )
        content_type = (resp_headers.get("content-type") or "").lower()
        if len(body) > self.settings.max_response_bytes:
            raise ValueError("Response too large")
        text = body.decode("utf-8", errors="replace")
        if "text/html" in content_type:
            return await self._process_html(
                url, text, status_code=status_code, options=options
            )
        meta = DocumentMetadata(
            source_url=url, status_code=status_code, content_type=content_type
        )
        return WebDocument(url=url, metadata=meta, text=text)

    async def _scrape_with_browser(
        self,
        url: str,
        *,
        headers: Dict[str, str],
        options: ScrapeOptions,
        timeout_s: float,
    ) -> WebDocument:
        _validate_http_url(url)
        browser = await self._ensure_browser()
        context = await browser.new_context(
            user_agent=headers.get(
                "User-Agent",
                self.settings.user_agent,
            ),
            viewport={"width": 1920, "height": 10000},
            service_workers="block",
        )
        await context.add_init_script(
            """
            for (const name of [
              "WebSocket",
              "EventSource",
              "RTCPeerConnection",
              "webkitRTCPeerConnection",
              "WebTransport",
            ]) {
              Object.defineProperty(globalThis, name, {
                configurable: false,
                value: class {
                  constructor() {
                    throw new Error(`${name} is disabled by BreadBoard`);
                  }
                }
              });
            }
            """
        )
        page = await context.new_page()
        blocked_request = False

        async def _proxy_route(route: Any) -> None:
            nonlocal blocked_request
            request = route.request
            method = str(request.method).upper()
            if method not in {"GET", "HEAD"}:
                blocked_request = True
                await route.abort("blockedbyclient")
                return
            try:
                destination = _resolve_http_destination(str(request.url))
                try:
                    request_headers = await request.all_headers()
                except Exception:
                    request_headers = dict(getattr(request, "headers", {}) or {})
                status_code, response_headers, body = await asyncio.to_thread(
                    self._request_once,
                    method,
                    destination,
                    headers=request_headers,
                    timeout_s=timeout_s,
                    verify_tls=not options.skip_tls_verification,
                )
                response_headers = {
                    key: value
                    for key, value in response_headers.items()
                    if key
                    not in {
                        "connection",
                        "content-length",
                        "transfer-encoding",
                    }
                }
            except _InvalidURLScheme:
                blocked_request = True
                await route.abort("blockedbyclient")
                return
            except Exception:
                await route.abort("failed")
                return
            await route.fulfill(
                status=status_code,
                headers=response_headers,
                body=body,
            )

        try:
            await page.route("**/*", _proxy_route)
            if options.headers:
                await page.set_extra_http_headers(options.headers)
            timeout_ms = int(max(1, timeout_s * 1000))
            try:
                response = await page.goto(
                    url,
                    timeout=timeout_ms * 2,
                    wait_until="domcontentloaded",
                )
            except Exception:
                if blocked_request:
                    raise _InvalidURLScheme(
                        "Only public http and https URLs are supported"
                    ) from None
                raise
            if blocked_request:
                raise _InvalidURLScheme("Only public http and https URLs are supported")
            _validate_http_url(str(page.url))
            if options.wait_for_ms is not None and int(options.wait_for_ms) > 0:
                await page.wait_for_timeout(int(options.wait_for_ms))
            if blocked_request:
                raise _InvalidURLScheme("Only public http and https URLs are supported")
            _validate_http_url(str(page.url))
            status_code = int(response.status) if response is not None else None
            html = await page.content()
            return await self._process_html(
                url,
                html,
                status_code=status_code,
                options=options,
            )
        finally:
            try:
                await page.close()
            except Exception:
                pass
            try:
                await context.close()
            except Exception:
                pass

    async def _scrape_file(
        self,
        url: str,
        *,
        headers: Dict[str, str],
        options: ScrapeOptions,
        timeout_s: float,
        content_type: str,
    ) -> WebDocument:
        status_code, resp_headers, body = await self._fetch_bytes(
            url,
            headers=headers,
            timeout_s=timeout_s,
            verify_tls=not options.skip_tls_verification,
        )
        content_type = (resp_headers.get("content-type") or content_type or "").lower()
        if len(body) > max(self.settings.max_response_bytes, 1):
            raise ValueError("Response too large")

        file_meta: Dict[str, Any] = {}
        if content_type.startswith("image/"):
            dims = await self._image_dimensions_fast(body)
            if dims:
                file_meta.update({"width": dims[0], "height": dims[1]})
        if content_type == "application/pdf":
            page_count = await self._pdf_page_count_fast(body)
            if page_count >= 0:
                file_meta["page_count"] = page_count
        meta = DocumentMetadata(
            source_url=url,
            status_code=status_code,
            content_type=content_type,
            file_metadata=file_meta or None,
        )

        content_base64 = None
        if options.include_file_body:
            content_base64 = base64.b64encode(body).decode("ascii")

        # Best-effort extraction for common types.
        extracted_text = None
        if content_type == "application/pdf":
            extracted_text = await self._pdf_text(body)
        else:
            extracted_text = body.decode("utf-8", errors="replace")

        return WebDocument(
            url=url, metadata=meta, text=extracted_text, content_base64=content_base64
        )

    async def _pdf_page_count_fast(self, body: bytes) -> int:
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception:
            return -1
        try:
            reader = await asyncio.to_thread(PdfReader, io.BytesIO(body), strict=False)
            return len(reader.pages)
        except Exception:
            return -1

    async def _pdf_text(self, body: bytes) -> str:
        try:
            from pypdf import PdfReader  # type: ignore
        except Exception:
            return ""
        try:
            reader = await asyncio.to_thread(PdfReader, io.BytesIO(body), strict=False)
            parts = []
            for page in reader.pages:
                try:
                    parts.append(page.extract_text() or "")
                except Exception:
                    continue
            return "\n\n".join([p.strip() for p in parts if p.strip()])
        except Exception:
            return ""

    async def _image_dimensions_fast(self, body: bytes) -> Optional[tuple[int, int]]:
        try:
            from PIL import Image  # type: ignore
        except Exception:
            return None
        try:
            with Image.open(io.BytesIO(body)) as img:
                return img.size
        except Exception:
            return None

    async def _process_html(
        self, url: str, html: str, *, status_code: Optional[int], options: ScrapeOptions
    ) -> WebDocument:
        try:
            from bs4 import BeautifulSoup  # type: ignore
            from markdownify import markdownify as md  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                f"HTML processing requires bs4+markdownify: {exc}"
            ) from exc

        soup = BeautifulSoup(html, "html.parser")
        raw_html = html
        original_meta = self._extract_metadata(soup, url=url, status_code=status_code)

        if options.include_tags:
            new_soup = BeautifulSoup("<html><body></body></html>", "html.parser")
            body = new_soup.body
            for selector in options.include_tags:
                for element in soup.select(selector):
                    body.append(element)
            soup = new_soup
        else:
            if options.only_main_content:
                soup = self._extract_main_content(soup)
            if options.exclude_tags:
                for selector in options.exclude_tags:
                    for element in soup.select(selector):
                        element.decompose()

        if options.remove_base64_images:
            for img in soup.find_all("img"):
                src = img.get("src", "")
                if src.startswith("data:image"):
                    img.decompose()

        if not options.use_relative_links:
            for a_tag in soup.find_all("a", href=True):
                a_tag["href"] = urljoin(url, a_tag["href"])

        meta = original_meta

        html_out = str(soup) if "html" in options.formats else None
        markdown_out = None
        text_out = None
        if any(fmt in options.formats for fmt in ("markdown", "content", "text")):
            markdown_out = md(
                str(soup),
                heading_style="ATX",
                bullets="-",
                strong_em_style="**",
                strip=["script", "style"],
            )
            markdown_out = self._postprocess_markdown(markdown_out)
            if meta.title and not re.match(r"^\s*#\s", markdown_out or ""):
                if markdown_out:
                    markdown_out = f"# {meta.title}\n\n{markdown_out}"
                else:
                    markdown_out = f"# {meta.title}\n"
            try:
                text_out = soup.get_text("\n", strip=True)
            except Exception:
                text_out = None

        links = []
        if "links" in options.formats:
            for link in soup.find_all("a", href=True):
                href = link.get("href") or ""
                if href and href not in links:
                    links.append(href)

        return WebDocument(
            url=url,
            metadata=meta,
            markdown=markdown_out,
            text=text_out,
            html=html_out,
            raw_html=raw_html if "rawHtml" in options.formats else None,
            links=links,
        )

    def _extract_metadata(
        self, soup, *, url: str, status_code: Optional[int]
    ) -> DocumentMetadata:
        title = None
        # Prefer <h1> if present (more human-friendly than <title> for many pages).
        try:
            h1 = soup.find("h1", attrs={"id": "firstHeading"}) or soup.find(
                "h1", attrs={"class": re.compile(r"\\bfirstHeading\\b")}
            )
            if h1 is None:
                h1 = soup.find("h1")
            if h1 is not None:
                title = h1.get_text(" ", strip=True) or None
        except Exception:
            title = None
        if not title:
            try:
                t = soup.find("title")
                if t:
                    title = t.get_text().strip() or None
            except Exception:
                title = None
        description = None
        try:
            desc_tag = soup.find("meta", attrs={"name": "description"})
            if desc_tag:
                description = (desc_tag.get("content") or "").strip() or None
        except Exception:
            description = None
        language = None
        try:
            html_tag = soup.find("html")
            if html_tag:
                language = html_tag.get("lang")
        except Exception:
            language = None

        return DocumentMetadata(
            title=title,
            description=description,
            language=language,
            source_url=url,
            status_code=status_code,
            content_type="text/html",
        )

    def _extract_main_content(self, soup):
        try:
            from bs4 import BeautifulSoup  # type: ignore
        except Exception:
            return soup

        selectors = [
            "#mw-content-text",
            "main",
            "article",
            "[role='main']",
            "#main",
            "#content",
        ]
        for selector in selectors:
            try:
                main_container = soup.select_one(selector)
            except Exception:
                main_container = None
            if main_container:
                new_soup = BeautifulSoup(str(main_container), "html.parser")
                for tag in new_soup(["script", "style"]):
                    tag.decompose()
                return new_soup
        # fallback: subtractive cleanup
        try:
            for script in soup(["script", "style", "nav", "header", "footer", "aside"]):
                script.decompose()
        except Exception:
            pass
        return soup

    def _postprocess_markdown(self, markdown_content: str) -> str:
        markdown_content = re.sub(
            r"!\[.*?\]\(data:image/[^;]+;base64,[A-Za-z0-9+/=]{100,}\)",
            "![Image content removed - base64 encoded]",
            markdown_content,
        )
        markdown_content = re.sub(
            r"\[Skip to Content\]\(#[^\)]*\)", "", markdown_content, flags=re.IGNORECASE
        )
        markdown_content = re.sub(r"\n\s*\n\s*\n", "\n\n", markdown_content)
        return markdown_content.strip()


def scrape_url_sync(
    url: str,
    *,
    options: Optional[ScrapeOptions] = None,
    settings: Optional[WebScraperSettings] = None,
) -> WebDocument:
    scraper = WebScraper(settings=settings)
    try:
        return asyncio.run(scraper.scrape_url(url, options=options))
    finally:
        try:
            asyncio.run(scraper.close())
        except Exception:
            pass
