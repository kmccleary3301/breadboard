from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Mapping

from breadboard.rl.phase3.evidence import sha256_file


class LocalObjectStore:
    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def _artifact_path(self, artifact_id: str) -> Path:
        if "/" in artifact_id or ".." in artifact_id:
            raise ValueError("artifact_id must be a flat identifier")
        return self.root / artifact_id

    def put_file(self, path: Path, *, artifact_id: str, metadata: Mapping[str, Any]) -> dict[str, Any]:
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(path)
        destination = self._artifact_path(artifact_id)
        shutil.copyfile(path, destination)
        stat = {"artifact_id": artifact_id, "path": str(destination), "sha256": sha256_file(destination), "bytes": destination.stat().st_size, "metadata": dict(metadata), "object_store": "local_object_store"}
        destination.with_suffix(destination.suffix + ".json").write_text(json.dumps(stat, sort_keys=True, indent=2) + "\n")
        return stat

    def get_file(self, artifact_id: str, destination: Path) -> Path:
        source = self._artifact_path(artifact_id)
        if not source.exists():
            raise FileNotFoundError(artifact_id)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        return destination

    def stat(self, artifact_id: str) -> dict[str, Any]:
        path = self._artifact_path(artifact_id)
        if not path.exists():
            raise FileNotFoundError(artifact_id)
        metadata_path = path.with_suffix(path.suffix + ".json")
        if metadata_path.exists():
            return json.loads(metadata_path.read_text())
        return {"artifact_id": artifact_id, "path": str(path), "sha256": sha256_file(path), "bytes": path.stat().st_size, "object_store": "local_object_store"}
