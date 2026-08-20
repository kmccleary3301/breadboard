"""Compatibility alias for the canonical provider routing module."""

from importlib import import_module
import sys

_module = import_module(".provider.routing", __package__)
sys.modules[__name__] = _module
