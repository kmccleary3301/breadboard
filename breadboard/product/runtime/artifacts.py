"""Content-addressed artifact ownership without machine-path references."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    digest: str
    size_bytes: int
    media_type: str

    def as_dict(self) -> dict[str, Any]:
        return {"digest": self.digest, "size_bytes": self.size_bytes, "media_type": self.media_type}


class ArtifactStore:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def put(self, content: bytes, *, media_type: str = "application/octet-stream") -> ArtifactRef:
        if not isinstance(content, bytes) or not media_type:
            raise TypeError("artifact content must be bytes and media_type must be populated")
        hexdigest = hashlib.sha256(content).hexdigest()
        ref = ArtifactRef(f"sha256:{hexdigest}", len(content), media_type)
        target = self._root / "sha256" / hexdigest[:2] / hexdigest
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if target.read_bytes() != content:
                raise RuntimeError("content-address collision")
            return ref
        temporary = target.with_name(f".{hexdigest}.{os.getpid()}.tmp")
        temporary.write_bytes(content)
        os.replace(temporary, target)
        return ref

    def put_json(self, value: Any) -> ArtifactRef:
        content = (json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ) + "\n").encode()
        return self.put(content, media_type="application/json")

    def read(self, ref: ArtifactRef) -> bytes:
        hexdigest = self._digest(ref)
        content = (self._root / "sha256" / hexdigest[:2] / hexdigest).read_bytes()
        if len(content) != ref.size_bytes or hashlib.sha256(content).hexdigest() != hexdigest:
            raise RuntimeError("artifact verification failed")
        return content

    def materialize(self, ref: ArtifactRef, destination: str | Path) -> Path:
        """Create a legacy path from verified content while retaining one write owner."""
        content = self.read(ref)
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        return target

    def manifest(self, session_id: str, artifacts: dict[str, ArtifactRef]) -> dict[str, Any]:
        if not session_id or any(not name or "/" in name or "\\" in name for name in artifacts):
            raise ValueError("manifest identity and artifact names must be portable")
        rows = [{"name": name, **ref.as_dict()} for name, ref in sorted(artifacts.items())]
        body = {"schema_version": "bb.artifact_manifest.v1",
                "session_id": session_id, "artifacts": rows}
        canonical = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        body["manifest_id"] = "artifact_manifest:" + hashlib.sha256(canonical).hexdigest()
        return body

    @staticmethod
    def _digest(ref: ArtifactRef) -> str:
        prefix, separator, hexdigest = ref.digest.partition(":")
        if (prefix != "sha256" or not separator or len(hexdigest) != 64
                or any(character not in "0123456789abcdef" for character in hexdigest)):
            raise ValueError("unsupported artifact digest")
        return hexdigest
