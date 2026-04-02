"""Compatibility alias for the canonical provider IR module."""

from importlib import import_module
import sys

_module = import_module(".provider.ir", __package__)
sys.modules[__name__] = _module
