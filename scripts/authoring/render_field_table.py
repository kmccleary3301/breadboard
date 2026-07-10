#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / "contracts" / "kernel" / "registries" / "config_surface_fields.v1.json"


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def render_field_table(registry_path: Path = DEFAULT_REGISTRY) -> str:
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    rows = []
    for entry in payload.get("entries", []):
        if not isinstance(entry, dict):
            continue
        metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
        rows.append(
            (
                str(entry.get("id") or ""),
                str(metadata.get("classification") or "unknown"),
                metadata.get("consumer") or "-",
                metadata.get("dossiers_using") if metadata.get("dossiers_using") is not None else "-",
            )
        )
    rows.sort(key=lambda row: (row[1] != "operational", row[0]))
    lines = [
        "| Field | Class | Runtime consumer | Public dossiers using it |",
        "|---|---|---|---:|",
    ]
    lines.extend(f"| `{_cell(field)}` | {_cell(kind)} | {_cell(consumer)} | {_cell(count)} |" for field, kind, consumer, count in rows)
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render the agent-config field registry as a Markdown table.")
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY), help="config_surface_fields registry JSON")
    args = parser.parse_args(argv)
    print(render_field_table(Path(args.registry)), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
