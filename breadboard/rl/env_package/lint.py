from __future__ import annotations

from pathlib import Path

from breadboard.rl.env_package.validate import load_yaml_mapping, validate_env_package_mapping


def lint_env_package(path: str | Path) -> list[str]:
    """Return human-readable EnvPackage validation errors for a YAML file."""

    try:
        payload = load_yaml_mapping(path)
    except Exception as exc:  # pragma: no cover - defensive CLI-facing path
        return [str(exc)]
    return validate_env_package_mapping(payload)
