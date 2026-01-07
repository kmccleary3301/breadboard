from __future__ import annotations

import uuid
from enum import Enum
from typing import Any, Dict, Tuple

from .sandbox_v2 import new_dev_sandbox_v2


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
        sandbox_cfg = (config or {}).get("sandbox") or {}
        driver = str((sandbox_cfg or {}).get("driver") or "process").strip().lower()
        options = dict((sandbox_cfg or {}).get("options") or {}) if isinstance(sandbox_cfg, dict) else {}

        actor = new_dev_sandbox_v2(
            image,
            str(workspace),
            name=f"sb-{session_id}",
            session_id=session_id,
            driver=driver,
            driver_options=options,
            lsp_actor=None,
        )
        return actor, session_id
