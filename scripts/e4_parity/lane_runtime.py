from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Literal, Mapping

try:
    from scripts.e4_parity.validators import hash_utils as _hash_utils
except ModuleNotFoundError:  # pragma: no cover - direct script execution
    from validators import hash_utils as _hash_utils


JsonStyle = Literal["default", "compact"]


def canonical_json(value: Any, *, separators_style: JsonStyle = "default") -> str:
    """Return canonical JSON text extracted from P3.1 and P3 remaining helper runtime builders."""

    if separators_style == "default":
        return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    if separators_style == "compact":
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    raise ValueError(f"unsupported separators_style: {separators_style!r}")


def display_path(path: Path, *, repo_root: Path) -> str:
    """Return the display path semantics shared by P3.1 and P3 remaining helper runtime builders."""

    resolved = path.resolve()
    root = repo_root.resolve()
    try:
        return resolved.relative_to(root).as_posix()
    except ValueError:
        try:
            return resolved.relative_to(root.parent).as_posix()
        except ValueError:
            return resolved.as_posix()


def sha256_file(path: Path) -> str:
    """Return prefixed file sha256 via the single hash truth source (validators.hash_utils)."""

    return _hash_utils.sha256_file(path)


def sha256_text(text: str) -> str:
    """Return prefixed UTF-8 text sha256 via the single hash truth source (validators.hash_utils)."""

    return _hash_utils.sha256_text(text)


def generated_at_utc(*, env_var: str = "BB_E4_GENERATED_AT_UTC", default: str) -> str:
    """Return env-first generated_at_utc for lane builders, with per-builder defaults."""

    return os.environ.get(env_var, default)


def write_json_artifact(path: Path, payload: Any, *, style: JsonStyle, trailing_newline: bool) -> str:
    """Write canonical JSON and return its sha, extracted from P3.1 and P3 remaining helper runtime builders."""

    text = canonical_json(payload, separators_style=style)
    if trailing_newline and not text.endswith("\n"):
        text += "\n"
    if not trailing_newline and text.endswith("\n"):
        text = text[:-1]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return sha256_text(text)


def upsert_ledger_row(
    ledger_path: Path,
    row: dict[str, Any],
    *,
    feature_id: str,
    style: JsonStyle = "default",
    trailing_newline: bool = True,
) -> str:
    """Upsert one ledger row as shared by P3.1 and P3 remaining helper runtime builders."""

    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    rows = [
        item
        for item in ledger.get("rows", [])
        if not (isinstance(item, Mapping) and item.get("feature_id") == feature_id)
    ]
    rows.append(row)
    ledger["rows"] = rows
    ledger["row_count"] = len(rows)
    write_json_artifact(ledger_path, ledger, style=style, trailing_newline=trailing_newline)
    return sha256_text(canonical_json({"row_id": feature_id, "row": row}, separators_style="compact"))


def upsert_ct_scenario(ct_scenarios_path: Path) -> None:
    """Upsert inventory CT scenarios shared by P3.1 and P3 remaining helper runtime builders."""

    try:
        from scripts.e4_parity.generate_ct_rows import upsert_inventory_scenarios
    except ModuleNotFoundError:  # pragma: no cover - direct script execution
        from generate_ct_rows import upsert_inventory_scenarios

    upsert_inventory_scenarios(ct_scenarios_path)
