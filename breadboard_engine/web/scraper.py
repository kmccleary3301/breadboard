from __future__ import annotations

import asyncio
import base64
import io
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urljoin

from .models import DocumentMetadata, ScrapeOptions, WebDocument


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
        return bool(options.render_js or options.wait_for_ms is not None or options.mobile)

    async def _ensure_browser(self):
        if self._browser is not None:
            return self._browser
        try:
            from playwright.async_api import async_playwright  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"Playwright not available for JS rendering: {exc}") from exc

        pw = await async_playwright().start()
        self._browser = await pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        return self._browser

    async def scrape_url(self, url: str, *, options: Optional[ScrapeOptions] = None) -> WebDocument:
        options = options or ScrapeOptions()
        headers: Dict[str, str] = {
            "User-Agent": self.settings.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            **(options.headers or {}),
        }

        timeout_s = options.timeout_s if options.timeout_s is not None else self.settings.default_timeout_s
        timeout_s = max(0.0, float(timeout_s))

        verify_tls = not bool(options.skip_tls_verification)

        # Try a HEAD to route HTML vs file. Some sites block particular client
        # TLS/HTTP fingerprints, so we fall back to `requests` when needed.
        content_type = ""
        try:
            head_status, head_headers = await self._fetch_headers(
                url,
                headers=headers,
                timeout_s=timeout_s,
                verify_tls=verify_tls,
            )
            if 200 <= head_status < 400:
                content_type = (head_headers.get("content-type") or "").lower()
            else:
                content_type = ""
        except Exception:
            content_type = ""

        try:
            if "text/html" in content_type or not content_type:
                if self._needs_browser_rendering(options):
                    return await self._scrape_with_browser(url, headers=headers, options=options, timeout_s=timeout_s)
                return await self._scrape_with_http(url, headers=headers, options=options, timeout_s=timeout_s)
            return await self._scrape_file(url, headers=headers, options=options, timeout_s=timeout_s, content_type=content_type)
        except Exception as exc:
            meta = DocumentMetadata(source_url=url, status_code=getattr(getattr(exc, "response", None), "status_code", None))
            return WebDocument(url=url, metadata=meta)

    async def _fetch_headers(
        self,
        url: str,
        *,
        headers: Dict[str, str],
        timeout_s: float,
        verify_tls: bool,
    ) -> tuple[int, Dict[str, str]]:
        """Return (status_code, headers) for HEAD with a requests fallback."""
        try:
            import httpx  # type: ignore

            async with httpx.AsyncClient(
                timeout=timeout_s,
                follow_redirects=True,
                verify=verify_tls,
            ) as client:
                resp = await client.head(url, headers=headers)
                # Some origins return 403 to httpx but 200 to requests.
                if resp.status_code != 403:
                    return int(resp.status_code), {k.lower(): v for k, v in resp.headers.items()}
        except Exception:
            pass

        def _requests_head() -> tuple[int, Dict[str, str]]:
            import requests  # type: ignore

            resp = requests.head(url, headers=headers, timeout=timeout_s, allow_redirects=True, verify=verify_tls)
            return int(resp.status_code), {k.lower(): v for k, v in resp.headers.items()}

        return await asyncio.to_thread(_requests_head)

    async def _fetch_bytes(
        self,
        url: str,
        *,
        headers: Dict[str, str],
        timeout_s: float,
        verify_tls: bool,
    ) -> tuple[int, Dict[str, str], bytes]:
        """Return (status_code, headers, body) for GET with a requests fallback."""
        try:
            import httpx  # type: ignore

            async with httpx.AsyncClient(
                timeout=timeout_s,
                follow_redirects=True,
                verify=verify_tls,
            ) as client:
                resp = await client.get(url, headers=headers)
                body = await resp.aread()
                # Some origins (e.g. Wikipedia in this environment) return 403 to
                # httpx but 200 to requests. Retry with requests on 403.
                if resp.status_code != 403:
                    return int(resp.status_code), {k.lower(): v for k, v in resp.headers.items()}, body
        except Exception:
            pass

        def _requests_get() -> tuple[int, Dict[str, str], bytes]:
            import requests  # type: ignore

            resp = requests.get(url, headers=headers, timeout=timeout_s, allow_redirects=True, verify=verify_tls)
            return int(resp.status_code), {k.lower(): v for k, v in resp.headers.items()}, resp.content

        return await asyncio.to_thread(_requests_get)

    async def _scrape_with_http(self, url: str, *, headers: Dict[str, str], options: ScrapeOptions, timeout_s: float) -> WebDocument:
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
            return await self._process_html(url, text, status_code=status_code, options=options)
        meta = DocumentMetadata(source_url=url, status_code=status_code, content_type=content_type)
        return WebDocument(url=url, metadata=meta, text=text)

    async def _scrape_with_browser(self, url: str, *, headers: Dict[str, str], options: ScrapeOptions, timeout_s: float) -> WebDocument:
        browser = await self._ensure_browser()
        context = await browser.new_context(
            user_agent=headers.get("User-Agent", self.settings.user_agent),
            viewport={"width": 1920, "height": 10000},
        )
        page = await context.new_page()
        try:
            if options.headers:
                await page.set_extra_http_headers(options.headers)
            timeout_ms = int(max(1, timeout_s * 1000))
            await page.goto(url, timeout=timeout_ms * 2, wait_until="domcontentloaded")
            if options.wait_for_ms is not None and int(options.wait_for_ms) > 0:
                await page.wait_for_timeout(int(options.wait_for_ms))
            html = await page.content()
            status_code = None
            try:
                resp = await page.wait_for_response(lambda r: r.url == url, timeout=1000)
                status_code = resp.status
            except Exception:
                status_code = None
            return await self._process_html(url, html, status_code=status_code, options=options)
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
        meta = DocumentMetadata(source_url=url, status_code=status_code, content_type=content_type, file_metadata=file_meta or None)

        content_base64 = None
        if options.include_file_body:
            content_base64 = base64.b64encode(body).decode("ascii")

        # Best-effort extraction for common types.
        extracted_text = None
        if content_type == "application/pdf":
            extracted_text = await self._pdf_text(body)
        else:
            extracted_text = body.decode("utf-8", errors="replace")

        return WebDocument(url=url, metadata=meta, text=extracted_text, content_base64=content_base64)

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

    async def _process_html(self, url: str, html: str, *, status_code: Optional[int], options: ScrapeOptions) -> WebDocument:
        try:
            from bs4 import BeautifulSoup  # type: ignore
            from markdownify import markdownify as md  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(f"HTML processing requires bs4+markdownify: {exc}") from exc

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

    def _extract_metadata(self, soup, *, url: str, status_code: Optional[int]) -> DocumentMetadata:
        title = None
        # Prefer <h1> if present (more human-friendly than <title> for many pages).
        try:
            h1 = soup.find("h1", attrs={"id": "firstHeading"}) or soup.find("h1", attrs={"class": re.compile(r"\\bfirstHeading\\b")})
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

        selectors = ["#mw-content-text", "main", "article", "[role='main']", "#main", "#content"]
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
        markdown_content = re.sub(r"\[Skip to Content\]\(#[^\)]*\)", "", markdown_content, flags=re.IGNORECASE)
        markdown_content = re.sub(r"\n\s*\n\s*\n", "\n\n", markdown_content)
        return markdown_content.strip()


def scrape_url_sync(url: str, *, options: Optional[ScrapeOptions] = None, settings: Optional[WebScraperSettings] = None) -> WebDocument:
    scraper = WebScraper(settings=settings)
    try:
        return asyncio.run(scraper.scrape_url(url, options=options))
    finally:
        try:
            asyncio.run(scraper.close())
        except Exception:
            pass

