"""Resolve authorized attachment media for provider-native requests."""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Any, Mapping

from breadboard.product.runtime.artifacts import ArtifactRef, read_workspace_artifact

from .contracts import ProviderContractError, ProviderRuntimeContext

_ATTACHMENT_URI = re.compile(r"attachment://(sha256:[0-9a-f]{64})")
_IMAGE_MIME = re.compile(r"image/[a-z0-9][a-z0-9.+-]*")


@dataclass(frozen=True, slots=True)
class ResolvedInputMedia:
    kind: str
    uri: str
    mime: str
    content: bytes

    @property
    def data_url(self) -> str:
        encoded = base64.b64encode(self.content).decode("ascii")
        return f"data:{self.mime};base64,{encoded}"

    @property
    def base64_data(self) -> str:
        return base64.b64encode(self.content).decode("ascii")


def resolve_input_media(
    block: Mapping[str, Any], context: ProviderRuntimeContext | None
) -> ResolvedInputMedia:
    """Resolve one canonical media block through its turn-scoped capability."""
    if not isinstance(block, Mapping) or block.get("type") != "media":
        raise ProviderContractError("provider input media block is malformed")
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


__all__ = ["ResolvedInputMedia", "resolve_input_media"]
