from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def _load_payload(path: Path) -> Any:
    if path.suffix.lower() in {".yaml", ".yml"}:
        try:
            import yaml  # type: ignore
        except Exception as exc:  # pragma: no cover - optional dependency
            raise RuntimeError("PyYAML is required for YAML optuna configs") from exc
        return yaml.safe_load(path.read_text(encoding="utf-8"))
    return json.loads(path.read_text(encoding="utf-8"))


def load_optuna_config(path: Path) -> Dict[str, Any]:
    payload = _load_payload(path)
    if not isinstance(payload, dict):
        raise ValueError("Optuna config must be a JSON/YAML object")
    space = payload.get("space")
    if not isinstance(space, dict):
        raise ValueError("Optuna config requires a 'space' object")
    study = payload.get("study") or {}
    if study and not isinstance(study, dict):
        raise ValueError("'study' must be an object if provided")
    fixed = payload.get("fixed_overrides") or {}
    if fixed and not isinstance(fixed, dict):
        raise ValueError("'fixed_overrides' must be an object if provided")
    return {
        "space": space,
        "study": study,
        "fixed_overrides": fixed,
    }
