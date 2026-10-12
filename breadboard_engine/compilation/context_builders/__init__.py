"""Profile-selected canonical startup messages, restricted to this package."""
from __future__ import annotations

from importlib import import_module, util
from pathlib import Path


def load_context_callback(configured, expected_callable):
    module = configured["module"]
    prefix = __name__ + "."
    if not isinstance(module, str) or not module.startswith(prefix):
        raise ValueError("context module must belong to the context_builders package")
    leaf = module[len(prefix):]
    if not leaf.isidentifier() or configured.get("callable") != expected_callable:
        raise ValueError(f"context requires a package module and {expected_callable} callback")
    specification = util.find_spec(module)
    if specification is None or specification.origin is None:
        raise ValueError("initial_context module is unavailable")
    if Path(specification.origin).resolve().parent != Path(__file__).resolve().parent:
        raise ValueError("initial_context module must resolve inside the repository package")
    return getattr(import_module(module), expected_callable)


def build_initial_context(owner, state, run_context, *, fresh, user_message):
    configured = (owner.config.get("prompts") or {}).get("initial_context")
    if configured is None:
        return []
    callback = load_context_callback(configured, "build_context")
    return callback(owner, state, run_context, fresh=fresh, user_message=user_message)
