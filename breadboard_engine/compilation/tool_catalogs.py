"""Canonical definition membership for the pinned OpenCode and OMO catalogs."""
from __future__ import annotations

from pathlib import Path, PurePosixPath


PINNED_CATALOGS = {"defs_opencode_1_2_17", "defs_omo_3_10_0"}


def catalog_directories(directory: str | Path) -> tuple[str, ...]:
    """Expand pinned catalogs without rewriting their admitted config paths."""
    path = PurePosixPath(str(directory))
    if path.name in PINNED_CATALOGS:
        return (str(path.with_name("defs_opencode_shared")), str(path))
    return (str(directory),)


def tool_definition_files(directory: str | Path) -> list[Path]:
    """Keep the original catalog's filename order across canonical resources."""
    files = [path for member in catalog_directories(directory)
             for path in Path(member).iterdir() if path.suffix in {".yaml", ".yml"}]
    return sorted(files, key=lambda path: path.name)
