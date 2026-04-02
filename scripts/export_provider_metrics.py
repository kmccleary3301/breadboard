#!/usr/bin/env python3
from __future__ import annotations

import importlib.util
import runpy
from pathlib import Path
import sys


_CANONICAL = Path(__file__).resolve().with_name("ops").joinpath("export_provider_metrics.py")


def _load_alias() -> None:
    spec = importlib.util.spec_from_file_location(__name__, _CANONICAL)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load canonical script module from {_CANONICAL}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[__name__] = module
    spec.loader.exec_module(module)


def main() -> None:
    runpy.run_path(
        str(_CANONICAL),
        run_name="__main__",
    )


if __name__ == "__main__":
    main()
else:
    _load_alias()
