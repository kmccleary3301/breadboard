"""Installed-resource loaders for product and internal operation authorities."""

from __future__ import annotations

import json
from importlib.resources import files
from typing import Any


def _load_catalog(package: str, filename: str) -> dict[str, Any]:
    resource = files(package).joinpath(filename)
    value = json.loads(resource.read_text(encoding="utf-8"))
    if not isinstance(value, dict) or not isinstance(value.get("operations"), list):
        raise ValueError(f"{package}/{filename}: operation catalog must contain an operations array")
    return value


def product_operation_catalog() -> dict[str, Any]:
    return _load_catalog("contracts.public", "operations.v2.json")


def internal_evidence_operation_catalog() -> dict[str, Any]:
    return _load_catalog("contracts.internal.evidence", "operations.v1.json")
