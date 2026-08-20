from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class HookResult:
    action: str
    reason: Optional[str] = None
    payload: Dict[str, Any] = field(default_factory=dict)

