from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ProviderError(RuntimeError):
    message: str
    status_code: Optional[int] = None
    payload: Any = None

    def __str__(self) -> str:
        if self.status_code is None:
            return self.message
        return f"{self.message} (status_code={self.status_code})"

