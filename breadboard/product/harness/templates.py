from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
import sysconfig
from typing import Any

from jsonschema import Draft202012Validator

from .model import HarnessDefinition
from .validate import load_harness_definition


DAILY_DRIVER_TEMPLATE_NAME = "daily_driver.v1.yaml"
DAILY_DRIVER_MODEL_ROLES_NAME = "daily_driver_roles.v1.json"
DAILY_DRIVER_PROMPT_BUNDLE_PATH = Path("prompts/daily_driver_system.md")
_TEMPLATE_ROOT = Path("agent_configs/templates")
_MODEL_ROLES_SCHEMA = Path("contracts/kernel/schemas/bb.model_roles.v1.schema.json")
_MINIMAL_TEMPLATE = _TEMPLATE_ROOT / "minimal_harness.v3.yaml"
_DAILY_DRIVER_TEMPLATE = _TEMPLATE_ROOT / DAILY_DRIVER_TEMPLATE_NAME
_DAILY_DRIVER_MODEL_ROLES = _TEMPLATE_ROOT / DAILY_DRIVER_MODEL_ROLES_NAME
_DAILY_DRIVER_PROMPT = _TEMPLATE_ROOT / DAILY_DRIVER_PROMPT_BUNDLE_PATH


def _data_path(relative_path: Path) -> Path:
    roots = (
        Path(__file__).resolve().parents[3],
        Path(sysconfig.get_path("data")).resolve(),
    )
    candidates = tuple(root / relative_path for root in roots)
    for root, candidate in zip(roots, candidates, strict=True):
        if any(
            root.joinpath(*relative_path.parts[:index]).is_symlink()
            for index in range(1, len(relative_path.parts) + 1)
        ):
            raise ValueError(
                "Harness package resources cannot traverse symlinks: "
                f"{relative_path.as_posix()}"
            )
        if candidate.is_file():
            return candidate
        if candidate.exists():
            raise IsADirectoryError(
                f"Harness package resource is not a file: "
                f"{relative_path.as_posix()}"
            )
    searched = ", ".join(map(str, candidates))
    raise FileNotFoundError(
        f"Harness package resource {relative_path.as_posix()} not found; "
        f"searched: {searched}"
    )


@lru_cache(maxsize=1)
def _model_roles_validator() -> Draft202012Validator:
    schema = json.loads(_data_path(_MODEL_ROLES_SCHEMA).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def minimal_template_path() -> Path:
    """Return the canonical template path in a checkout or installed distribution."""
    return _data_path(_MINIMAL_TEMPLATE)


def minimal_template_text() -> str:
    """Return the checked-in canonical template without transforming it."""
    return minimal_template_path().read_text(encoding="utf-8")


def load_minimal_harness() -> HarnessDefinition:
    """Load and strictly validate the canonical minimal Harness Definition."""
    return load_harness_definition(minimal_template_path())


def daily_driver_template_path() -> Path:
    """Return the ordinary daily-driver profile path."""
    return _data_path(_DAILY_DRIVER_TEMPLATE)


def daily_driver_template_text() -> str:
    """Return the ordinary daily-driver profile without transforming it."""
    return daily_driver_template_path().read_text(encoding="utf-8")


def daily_driver_model_roles_path() -> Path:
    """Return the daily-driver model-role resource path."""
    return _data_path(_DAILY_DRIVER_MODEL_ROLES)


def daily_driver_model_roles_text() -> str:
    """Return the daily-driver model-role resource without transforming it."""
    return daily_driver_model_roles_path().read_text(encoding="utf-8")


def load_daily_driver_model_roles(path: Path | None = None) -> dict[str, Any]:
    """Load and strictly validate a daily-driver model-role resource."""
    target = daily_driver_model_roles_path() if path is None else Path(path)
    document = json.loads(target.read_text(encoding="utf-8"))
    errors = sorted(
        _model_roles_validator().iter_errors(document),
        key=lambda error: tuple(str(part) for part in error.absolute_path),
    )
    if errors:
        error = errors[0]
        pointer = "/" + "/".join(str(part) for part in error.absolute_path)
        raise ValueError(
            f"invalid daily-driver model roles at {pointer or '/'}: {error.message}"
        )
    if not isinstance(document, dict):
        raise ValueError("daily-driver model roles must be an object")
    return document


def daily_driver_prompt_path() -> Path:
    """Return the daily-driver system-prompt resource path."""
    return _data_path(_DAILY_DRIVER_PROMPT)


def daily_driver_prompt_text() -> str:
    """Return the daily-driver system prompt without transforming it."""
    return daily_driver_prompt_path().read_text(encoding="utf-8")


def load_daily_driver_harness() -> HarnessDefinition:
    """Load and strictly validate the ordinary daily-driver profile."""
    return load_harness_definition(daily_driver_template_path())
