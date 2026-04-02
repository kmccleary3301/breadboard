"""Compatibility alias for the canonical conductor plan-bootstrapper module."""

from importlib import import_module
import sys

_module = import_module(".conductor.plan_bootstrapper", __package__)
sys.modules[__name__] = _module
