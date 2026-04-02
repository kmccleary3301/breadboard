"""Compatibility alias for the canonical conductor streaming-policy module."""

from importlib import import_module
import sys

_module = import_module(".conductor.streaming_policy", __package__)
sys.modules[__name__] = _module
