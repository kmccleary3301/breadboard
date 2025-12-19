from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class ScrapeOptions:
    formats: List[str] = field(default_factory=lambda: ["markdown"])
    headers: Optional[Dict[str, str]] = None
    include_tags: Optional[List[str]] = None
    exclude_tags: Optional[List[str]] = None
    only_main_content: bool = True
    wait_for_ms: Optional[int] = None
    timeout_s: Optional[float] = None
    mobile: bool = False
    skip_tls_verification: bool = False
    remove_base64_images: bool = True
    use_relative_links: bool = False
    include_file_body: bool = False
    render_js: bool = False


@dataclass(frozen=True)
class DocumentMetadata:
    source_url: Optional[str] = None
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    language: Optional[str] = None
    file_metadata: Optional[Dict[str, Any]] = None
    blocked: Optional[bool] = None
    blocked_reason: Optional[str] = None


@dataclass(frozen=True)
class WebDocument:
    url: str
    metadata: DocumentMetadata = field(default_factory=DocumentMetadata)
    markdown: Optional[str] = None
    text: Optional[str] = None
    html: Optional[str] = None
    raw_html: Optional[str] = None
    links: List[str] = field(default_factory=list)
    content_base64: Optional[str] = None

