from __future__ import annotations

import importlib
from pathlib import Path


def test_only_canonical_run_logging_package_remains() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    canonical = importlib.import_module(
        "breadboard_engine.run_logging.provider_native_logger"
    )

    assert hasattr(canonical, "ProviderNativeLogger")
    assert not (repo_root / "breadboard_engine" / "logging_v2").exists()
    assert not (
        repo_root / "breadboard_engine" / "legacy" / "logging_v2"
    ).exists()
