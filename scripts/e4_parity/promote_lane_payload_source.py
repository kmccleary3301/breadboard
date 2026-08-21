#!/usr/bin/env python3
"""One-time reproducible promotion of legacy generated payload blocks to authoring input."""
from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml


class PayloadSourceError(ValueError):
    pass


def validate_payload_source(value: Any, *, source: Path | None = None) -> dict[str, Any]:
    prefix = f"{source}: " if source is not None else ""
    if not isinstance(value, Mapping) or set(value) != {"payload_templates", "substitutions"}:
        raise PayloadSourceError(prefix + "payload source must contain exactly payload_templates and substitutions")
    for field in ("payload_templates", "substitutions"):
        if not isinstance(value[field], Mapping):
            raise PayloadSourceError(prefix + f"{field} must be an object")
    return {"payload_templates": dict(value["payload_templates"]), "substitutions": dict(value["substitutions"])}


def extract_payload_source(legacy_path: Path) -> dict[str, Any]:
    legacy = yaml.safe_load(legacy_path.read_text(encoding="utf-8"))
    normalize = legacy.get("normalize") if isinstance(legacy, Mapping) else None
    config = normalize.get("config") if isinstance(normalize, Mapping) else None
    constants = config.get("packet_constants") if isinstance(config, Mapping) else None
    if not isinstance(constants, Mapping):
        raise PayloadSourceError(f"{legacy_path}: missing normalize.config.packet_constants")
    return validate_payload_source(
        {
            "payload_templates": constants.get("payload_templates"),
            "substitutions": constants.get("substitutions"),
        },
        source=legacy_path,
    )


def canonical_source_bytes(value: Mapping[str, Any]) -> bytes:
    validated = validate_payload_source(value)
    return (
        json.dumps(
            validated,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("legacy", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    try:
        expected = canonical_source_bytes(extract_payload_source(args.legacy))
    except (OSError, yaml.YAMLError, PayloadSourceError) as exc:
        parser.error(str(exc))
    if args.check:
        return 0 if args.output.is_file() and args.output.read_bytes() == expected else 5
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
