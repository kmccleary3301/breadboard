"""
Todo tooling primitives for KyleCode.

Provides data models, persistence helpers, and manager utilities that back the
provider-agnostic todo tool contract introduced for OpenCode/Claude parity.
"""

from .store import (
    Todo,
    TodoDraft,
    TodoPatch,
    TodoRef,
    TodoNote,
    TodoEvent,
    TodoStore,
)
from .manager import TodoManager

__all__ = [
    "Todo",
    "TodoDraft",
    "TodoPatch",
    "TodoRef",
    "TodoNote",
    "TodoEvent",
    "TodoStore",
    "TodoManager",
]
