"""Compatibility alias for the canonical provider runtime module."""

from importlib import import_module
import sys

_module = import_module(".provider.runtime", __package__)
sys.modules[__name__] = _module
