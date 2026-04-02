"""Compatibility alias for the canonical conductor context-window guard module."""

from importlib import import_module
import sys

_module = import_module(".conductor.context_window_guard", __package__)
sys.modules[__name__] = _module
