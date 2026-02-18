#!/usr/bin/env python3
"""Preflight workspace safety checks before running BreadBoard tasks.

This script prevents catastrophic deletions caused by dangerous workspace roots
(`.`, `..`, repo root, ancestors, home, temp root).
"""

from __future__ import annotations

import argparse
import json
import os
import tempfile
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]


def _is_within(base: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(base)
        return True
    except ValueError:
        return False


def _load_config(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        payload = json.loads(text)
        return payload if isinstance(payload, dict) else {}
    try:
        import yaml  # type: ignore
    except Exception:
        return {}
    payload = yaml.safe_load(text)
    return payload if isinstance(payload, dict) else {}


def _resolve_workspace(config_path: Path, workspace_arg: str | None) -> Path:
    raw: str | None = workspace_arg
    if not raw:
        cfg = _load_config(config_path)
        ws = cfg.get("workspace")
        if isinstance(ws, dict):
            candidate = ws.get("root")
            if isinstance(candidate, str) and candidate.strip():
                raw = candidate.strip()
    if not raw:
        # Default mirrors agent behavior.
        raw = f"agent_ws_{config_path.stem}"
    workspace = Path(raw).expanduser()
    if not workspace.is_absolute():
        workspace = (REPO_ROOT / workspace)
    return workspace.resolve()


def _validate_workspace(path: Path) -> tuple[bool, str]:
    home = Path.home().resolve()
    tmp_root = Path(tempfile.gettempdir()).resolve()
    repo = REPO_ROOT.resolve()

    if path == Path("/"):
        return False, "workspace is filesystem root '/'"
    if path == repo:
        return False, f"workspace resolves to repo root '{repo}'"
    if path in repo.parents:
        return False, f"workspace resolves to repo ancestor '{path}'"
    if path == home:
        return False, "workspace resolves to home directory"
    if path == tmp_root:
        return False, "workspace resolves to temp root"
    if (path / ".git").exists():
        return False, "workspace contains .git"

    if not (_is_within(repo, path) or _is_within(tmp_root, path)):
        if os.environ.get("BREADBOARD_ALLOW_UNSAFE_WORKSPACE") != "1":
            return False, (
                f"workspace '{path}' is outside repo/tmp. "
                "Set BREADBOARD_ALLOW_UNSAFE_WORKSPACE=1 to override."
            )
    return True, "ok"


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate workspace root safety for BreadBoard task runs.")
    parser.add_argument("--config", required=True, help="Path to agent config file (yaml/json).")
    parser.add_argument(
        "--workspace",
        default="",
        help="Explicit workspace root to validate (overrides config.workspace.root).",
    )
    args = parser.parse_args()

    config_path = Path(args.config).expanduser()
    if not config_path.is_absolute():
        config_path = (Path.cwd() / config_path)
    config_path = config_path.resolve()
    if not config_path.exists():
        print(f"[preflight] FAIL: config not found: {config_path}")
        return 2

    workspace = _resolve_workspace(config_path, args.workspace or None)
    ok, reason = _validate_workspace(workspace)
    if ok:
        print(f"[preflight] OK: workspace={workspace}")
        return 0
    print(f"[preflight] FAIL: workspace={workspace} ({reason})")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
