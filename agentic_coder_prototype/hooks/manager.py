from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_hook_manager(config: Dict[str, Any], workspace: str, *, plugin_manifests: Optional[List[Any]] = None) -> Any:
    # Hooking is optional. For now return None unless explicitly enabled later.
    return None

