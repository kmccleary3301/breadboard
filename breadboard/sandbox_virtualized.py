from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Dict, Tuple

from .sandbox_v2 import DevSandboxV2


class DeploymentMode(str, Enum):
    DEVELOPMENT = "development"
    PRODUCTION = "production"


class SandboxFactory:
    """Minimal sandbox factory for virtualized deployments."""

    def create_sandbox(self, mode: DeploymentMode, config: Dict[str, Any]) -> Tuple[Any, str]:
        session_id = f"virtual-{uuid.uuid4()}"
        runtime_cfg = (config or {}).get("runtime") or {}
        workspace = (config or {}).get("workspace") or "."
        image = runtime_cfg.get("image", "python-dev:latest")
        actor = DevSandboxV2.options(name=f"sb-{session_id}").remote(
            image=image,
            session_id=session_id,
            workspace=str(workspace),
            lsp_actor=None,
        )
        return actor, session_id
