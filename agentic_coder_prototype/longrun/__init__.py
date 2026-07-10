"""Long-running controller package (Phase 1 scaffolding)."""

from __future__ import annotations

import importlib

from .checkpoint import load_latest_checkpoint_pointer, load_state_from_latest_checkpoint, write_checkpoint
from .flags import is_longrun_enabled, resolve_episode_max_steps

_LAZY_EXPORTS = {
    "FeatureFileQueue": (".queue", "FeatureFileQueue"),
    "LongRunController": (".controller", "LongRunController"),
    "RecoveryPolicy": (".recovery", "RecoveryPolicy"),
    "TodoStoreQueue": (".queue", "TodoStoreQueue"),
    "VerificationPolicy": (".verification", "VerificationPolicy"),
    "build_work_queue": (".queue", "build_work_queue"),
}

__all__ = [
    "FeatureFileQueue",
    "LongRunController",
    "RecoveryPolicy",
    "TodoStoreQueue",
    "VerificationPolicy",
    "build_work_queue",
    "is_longrun_enabled",
    "load_latest_checkpoint_pointer",
    "load_state_from_latest_checkpoint",
    "resolve_episode_max_steps",
    "write_checkpoint",
]


def __getattr__(name: str):
    if name not in _LAZY_EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

    module_name, attr_name = _LAZY_EXPORTS[name]
    value = getattr(importlib.import_module(module_name, __name__), attr_name)
    globals()[name] = value
    return value
