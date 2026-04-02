"""Compatibility alias for the canonical permissions policy-pack module."""

from importlib import import_module
import sys

_module = import_module(".permissions.policy_pack", __package__)
sys.modules[__name__] = _module
