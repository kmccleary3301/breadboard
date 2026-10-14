"""Canonical provider images and authorized attachment resolution."""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from urllib.parse import urlsplit
from typing import Any, Mapping

from breadboard.product.runtime.artifacts import (
    ArtifactRef,
    read_workspace_artifact,
)

from .contracts import ProviderContractError, ProviderRuntimeContext

_ATTACHMENT_URI = re.compile(r"attachment://(sha256:[0-9a-f]{64})")
_IMAGE_MIME = re.compile(r"image/[a-z0-9][a-z0-9.+-]*")
_MISSING_DETAIL = object()


@dataclass(frozen=True, slots=True)
class ResolvedInputMedia:
    kind: str
    uri: str
    mime: str
    content: bytes
    wire_url: str | None = None
    detail: str | None = None

    @property
    def data_url(self) -> str:
        if self.wire_url is not None:
            return self.wire_url
        encoded = base64.b64encode(self.content).decode("ascii")
        return f"data:{self.mime};base64,{encoded}"

    @property
    def base64_data(self) -> str:
        if self.wire_url is not None:
            raise ProviderContractError("image URL cannot be re-encoded")
        return base64.b64encode(self.content).decode("ascii")


def resolve_input_media(
    block: Mapping[str, Any], context: ProviderRuntimeContext | None
) -> ResolvedInputMedia:
    """Resolve one canonical media block through its turn-scoped capability."""
    if not isinstance(block, Mapping) or block.get("type") != "media":
        raise ProviderContractError("provider input media block is malformed")
    if block.get("kind") == "codex_image_url":
        uri = block.get("uri")
        detail = block.get("detail", "high")
        if not isinstance(uri, str) or detail not in {"auto", "low", "high", "original"}:
            raise ProviderContractError("malformed Codex image URL")
        state = context.session_state if context is not None else None
        config = getattr(state, "config", {}) or {}
        native_codex = (
            (config.get("tools") or {}).get("native_semantics")
            == "codex_0_139"
        )
        extra = context.extra if context is not None else {}
        portable_urls = (
            isinstance(extra, Mapping)
            and extra.get("portable_image_urls") is True
        )
        if native_codex:
            return ResolvedInputMedia(
                "image", uri, "", b"", wire_url=uri, detail=detail,
            )
        if portable_urls:
            return parse_provider_image_url(uri, detail=detail)
        raise ProviderContractError(
            "remote image URL requires the native Codex profile"
        )
    if block.get("kind") != "image":
        raise ProviderContractError("provider input media kind must be image")
    uri = block.get("uri")
    mime = block.get("mime")
    match = _ATTACHMENT_URI.fullmatch(uri) if isinstance(uri, str) else None
    if match is None:
        raise ProviderContractError(
            "provider input media requires an authorized attachment URI"
        )
    if not isinstance(mime, str) or _IMAGE_MIME.fullmatch(mime) is None:
        raise ProviderContractError(
            "provider input media requires a canonical image media type"
        )
    if context is None:
        raise ProviderContractError("provider input media requires runtime context")
    session_state = context.session_state
    capabilities = (
        session_state.get_provider_metadata("attachment_capabilities", {})
        if session_state is not None
        and hasattr(session_state, "get_provider_metadata")
        else {}
    )
    trusted = capabilities.get(uri) if isinstance(capabilities, Mapping) else None
    if not isinstance(trusted, Mapping):
        raise ProviderContractError(
            "provider input media attachment is not authorized for this turn"
        )
    try:
        artifact = ArtifactRef(
            digest=str(trusted["digest"]),
            size_bytes=int(trusted["size_bytes"]),
            media_type=str(trusted["media_type"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderContractError(
            "provider input media capability is malformed"
        ) from exc
    if artifact.digest != match.group(1) or artifact.media_type != mime:
        raise ProviderContractError(
            "provider input media does not match its authorized capability"
        )
    workspace = getattr(session_state, "workspace", None)
    if not isinstance(workspace, str) or not workspace:
        raise ProviderContractError(
            "provider input media requires an authorized workspace"
        )
    try:
        content = read_workspace_artifact(workspace, artifact)
    except Exception as exc:
        raise ProviderContractError(
            "provider input media artifact verification failed"
        ) from exc
    return ResolvedInputMedia(
        kind="image",
        uri=uri,
        mime=mime,
        content=content,
    )


def parse_provider_image_url(
    image_url: str, *, detail: str = "high",
) -> ResolvedInputMedia:
    """Parse an admitted image URL into bytes or a validated remote URL."""
    if not isinstance(image_url, str):
        raise ProviderContractError("provider image URL must be text")
    if image_url.startswith("data:"):
        header, separator, encoded = image_url.partition(",")
        metadata = header[5:].split(";") if header.startswith("data:") else []
        if (
            not separator
            or len(metadata) != 2
            or metadata[1] != "base64"
            or not _IMAGE_MIME.fullmatch(metadata[0])
        ):
            raise ProviderContractError(
                "provider image must be a base64 image data URL"
            )
        try:
            content = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError, TypeError) as exc:
            raise ProviderContractError(
                "provider image data URL is malformed"
            ) from exc
        if not content:
            raise ProviderContractError("provider image data URL is empty")
        return ResolvedInputMedia(
            "image", image_url, metadata[0], content, detail=detail,
        )
    try:
        parsed = urlsplit(image_url)
        valid_url = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname is not None
            and parsed.username is None
            and parsed.password is None
        )
    except ValueError:
        valid_url = False
    if not valid_url:
        raise ProviderContractError(
            "provider image must be an HTTP(S) URL or data URL"
        )
    return ResolvedInputMedia(
        "image", image_url, "", b"", wire_url=image_url, detail=detail,
    )


def _image_url_media(
    image_url: str, *, detail: Any = _MISSING_DETAIL,
) -> dict[str, Any]:
    if not isinstance(image_url, str):
        raise ProviderContractError("provider image URL must be text")
    media: dict[str, Any] = {
        "type": "media",
        "kind": "codex_image_url",
        "uri": image_url,
    }
    if detail is not _MISSING_DETAIL:
        media["detail"] = detail
    return media



def is_provider_image_part(block: Mapping[str, Any]) -> bool:
    kind = block.get("type")
    return kind in {"image_url", "input_image", "image"} or (
        kind == "media" and block.get("kind") in {"image", "codex_image_url"}
    )


def portable_image_media(block: Mapping[str, Any]) -> dict[str, Any]:
    """Represent a provider image with the canonical media carrier."""
    kind = block.get("type")
    if kind == "media":
        return dict(block)
    detail = block.get("detail", _MISSING_DETAIL)
    if kind == "image":
        source = block.get("source")
        if not isinstance(source, Mapping):
            raise ProviderContractError("provider image source is malformed")
        if source.get("type") == "base64":
            mime = source.get("media_type")
            encoded = source.get("data")
            if not isinstance(mime, str) or not _IMAGE_MIME.fullmatch(mime):
                raise ProviderContractError("provider image media type is invalid")
            if not isinstance(encoded, str):
                raise ProviderContractError("provider image data must be text")
            return _image_url_media(f"data:{mime};base64,{encoded}", detail=detail)
        if source.get("type") == "url" and isinstance(source.get("url"), str):
            return _image_url_media(source["url"], detail=detail)
        raise ProviderContractError("provider image source is not portable")
    image_url = block.get("image_url")
    if isinstance(image_url, Mapping):
        detail = image_url.get("detail", detail)
        image_url = image_url.get("url")
    if not isinstance(image_url, str):
        raise ProviderContractError("provider image URL is malformed")
    return _image_url_media(image_url, detail=detail)



__all__ = [
    "ResolvedInputMedia",
    "is_provider_image_part",
    "parse_provider_image_url",
    "portable_image_media",
    "resolve_input_media",
]
