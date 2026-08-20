"""Compatibility alias for the canonical conductor loop-detection module."""

from importlib import import_module
import sys

_module = import_module(".conductor.loop_detection", __package__)
sys.modules[__name__] = _module
