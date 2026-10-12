"""Portable locations and selective guards for optional pinned stock runtimes."""
from __future__ import annotations

import importlib.util
import os
import shutil
import site
from pathlib import Path

import pytest

_CACHE = Path.home() / ".cache/bb-compaction-e4"
_HERMES_REVISION = "939e45c91d751fadd94dcd1b873ac3cb44846213"


def resolve_omp(version: str = "18.1.17") -> Path:
    env_name = "OMP16213_CODING_AGENT_NODE_MODULES" if version == "16.2.13" else "OMP_CODING_AGENT_NODE_MODULES"
    configured = os.environ.get(env_name) or os.environ.get("OMP_DIR")
    return Path(configured) if configured else _CACHE / "pkgs" / f"@oh-my-pi__pi-coding-agent@{version}" / "node_modules"


def require_omp(version: str = "18.1.17") -> Path:
    modules = resolve_omp(version)
    if not (modules / "@oh-my-pi/pi-coding-agent/src/sdk.ts").is_file():
        pytest.skip(f"pinned OMP {version} package is unavailable at {modules}")
    return modules


def resolve_openclaw() -> Path:
    configured = os.environ.get("OPENCLAW_DIST")
    return Path(configured) if configured else _CACHE / "pkgs/openclaw@2026.9.4/node_modules/openclaw/dist"


def require_openclaw() -> Path:
    dist = resolve_openclaw()
    if not (dist / "resource-loader-Bu_pVD2t.mjs").is_file():
        pytest.skip(f"pinned OpenClaw 2026.9.4 dist is unavailable at {dist}")
    if shutil.which("node") is None:
        pytest.skip("Node is unavailable for pinned OpenClaw")
    return dist


def require_hermes() -> Path:
    configured = os.environ.get("HERMES_SOURCE_ROOT")
    source = Path(configured) if configured else _CACHE / f"hermes-agent-{_HERMES_REVISION}"
    if not (source / "agent/context_compressor.py").is_file():
        pytest.skip(f"pinned Hermes source is unavailable at {source}")
    return source


def require_hermes_worker_runtime() -> None:
    """Skip unless the Hermes worker can initialize stock Hermes in this interpreter.

    Outside a sealed native root the worker reads the pinned source from the
    user cache and admits SDK modules only from the interpreter's primary
    site-packages (``hermes_worker.py`` ``_materialize_environment``).
    """
    from breadboard.rl.harness import hermes_worker

    source = _CACHE / hermes_worker._SOURCE_ID
    if not (source / "run_agent.py").is_file():
        pytest.skip(f"pinned Hermes source is unavailable at {source}")
    primary = Path(site.getsitepackages()[0]).resolve()
    for name in ("httpx", "openai"):
        spec = importlib.util.find_spec(name)
        if spec is None or spec.origin is None or not Path(spec.origin).resolve().is_relative_to(primary):
            pytest.skip(f"the Hermes worker admits SDK modules only from {primary}; {name} is not installed there")


def resolve_pi(version: str) -> Path:
    env_name = "PI057_CODING_AGENT_NODE_MODULES" if version == "0.57.1" else "PI_CODING_AGENT_NODE_MODULES"
    configured = os.environ.get(env_name)
    return Path(configured) if configured else _CACHE / "pkgs" / f"@mariozechner__pi-coding-agent@{version}" / "node_modules"
