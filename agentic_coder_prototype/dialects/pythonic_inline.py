from __future__ import annotations

from .pythonic02 import Pythonic02Dialect


class PythonicInlineDialect(Pythonic02Dialect):
    """Inline pythonic tool call dialect (alias of Pythonic02)."""

    def prompt_for_tools(self, tools):
        return super().prompt_for_tools(tools)
