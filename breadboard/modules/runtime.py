"""Public runtime declaration for the installed BreadBoard worker image."""
from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
import re
from typing import TypeAlias

WORKER_MODULE = "breadboard.modules.worker"
_RUNTIME_VALUE: TypeAlias = str | tuple[str, ...]
_OCI_REF = re.compile(r"^(?:sha256:[0-9a-f]{64}|[^\s@]+@sha256:[0-9a-f]{64})$")
_LINUX_PLATFORM = re.compile(r"^linux/[a-z0-9_]+(?:/[a-z0-9_.-]+)?$")


def oci_worker_runtime(image_ref: str, platform: str) -> Mapping[str, _RUNTIME_VALUE]:
    """Return an immutable manifest runtime mapping for an installed SDK image.

    ``image_ref`` is a registry digest or an exact local Docker config ID.  This
    helper deliberately takes the platform explicitly so an OCI receiver cannot
    select another image variant.  The entrypoint runs the SDK installed in that
    image; it captures no controller source and uses no author bootstrap script.
    """
    if not isinstance(image_ref, str) or _OCI_REF.fullmatch(image_ref) is None:
        raise ValueError("worker OCI image must be a pinned digest or local config ID")
    if not isinstance(platform, str) or _LINUX_PLATFORM.fullmatch(platform) is None:
        raise ValueError("worker OCI platform must be an explicit Linux platform")
    return MappingProxyType(
        {
            "kind": "oci",
            "ref": image_ref,
            "platform": platform,
            "entrypoint": ("python3", "-I", "-m", WORKER_MODULE),
        }
    )


__all__ = ["oci_worker_runtime"]
