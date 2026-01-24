from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple
import os

import yaml

from .parity import EquivalenceLevel

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = REPO_ROOT / "misc" / "opencode_runs" / "parity_scenarios.yaml"


def _fixture_root() -> Optional[Path]:
    raw = os.environ.get("BREADBOARD_FIXTURE_ROOT") or os.environ.get("BREADBOARD_PARITY_FIXTURE_ROOT")
    if not raw:
        return None
    return Path(raw).expanduser().resolve()


@dataclass(frozen=True)
class ParityScenario:
    name: str
    config: Path
    mode: str
    session: Optional[Path] = None
    task: Optional[Path] = None
    script: Optional[Path] = None
    script_output: Optional[Path] = None
    todo_expected: Optional[Path] = None
    guardrails_expected: Optional[Path] = None
    golden_workspace: Optional[Path] = None
    workspace_seed: Optional[Path] = None
    golden_meta: Optional[Path] = None
    equivalence: EquivalenceLevel = EquivalenceLevel.SEMANTIC
    description: Optional[str] = None
    tags: Tuple[str, ...] = tuple()
    guard_fixture: Optional[Path] = None
    enabled: bool = True
    fail_mode: str = "fail"
    max_steps: Optional[int] = None
    logging_root: Optional[Path] = None
    gate_tier: str = "E1"
    trophy_tier: Optional[str] = None


def _resolve_path(value: Optional[str]) -> Optional[Path]:
    if not value:
        return None
    candidate = Path(value)
    if not candidate.is_absolute():
        fixture_root = _fixture_root()
        if fixture_root:
            fixture_candidate = (fixture_root / value).resolve()
            if fixture_candidate.exists():
                return fixture_candidate
            if value.startswith("misc/"):
                trimmed = value[len("misc/") :]
                fixture_trimmed = (fixture_root / trimmed).resolve()
                if fixture_trimmed.exists():
                    return fixture_trimmed
        candidate = (REPO_ROOT / candidate).resolve()
    return candidate


def _coerce_bool(value: object, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "auto", "default"}:
            return default
        if normalized in {"false", "0", "no", "disable", "disabled"}:
            return False
        if normalized in {"true", "1", "yes", "enable", "enabled"}:
            return True
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return default


def load_parity_scenarios(manifest_path: Optional[Path] = None) -> List[ParityScenario]:
    path = manifest_path or DEFAULT_MANIFEST
    if not path.exists():
        raise FileNotFoundError(path)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    raw_scenarios = data.get("scenarios") or []
    scenarios: List[ParityScenario] = []
    for entry in raw_scenarios:
        if not isinstance(entry, dict):
            continue
        name = str(entry.get("name") or "").strip()
        config = _resolve_path(entry.get("config"))
        session = _resolve_path(entry.get("session"))
        task = _resolve_path(entry.get("task"))
        golden_workspace = _resolve_path(entry.get("golden_workspace"))
        workspace_seed = _resolve_path(entry.get("workspace_seed") or entry.get("seed_workspace"))
        golden_meta = _resolve_path(entry.get("summary")) or _resolve_path(entry.get("golden_meta"))
        mode = str(entry.get("mode") or "replay").lower()
        if not (name and config):
            continue
        script = _resolve_path(entry.get("script"))
        script_output = _resolve_path(entry.get("script_output"))
        guard_fixture = _resolve_path(entry.get("guard_fixture"))
        description = str(entry.get("description") or "").strip() or None
        raw_tags = entry.get("tags") or []
        tags: Tuple[str, ...] = ()
        if isinstance(raw_tags, str):
            tags = tuple(tag.strip() for tag in raw_tags.split(",") if tag.strip())
        elif isinstance(raw_tags, (list, tuple)):
            tags = tuple(str(tag).strip() for tag in raw_tags if str(tag).strip())
        enabled = _coerce_bool(entry.get("enabled"), default=True)
        if enabled:
            if mode == "replay" and (session is None or golden_workspace is None):
                continue
            if mode == "task" and (task is None or golden_workspace is None):
                continue
        todo_expected = _resolve_path(entry.get("todo_expected"))
        guardrails_expected = _resolve_path(entry.get("guardrails_expected"))
        raw_level = str(entry.get("equivalence") or EquivalenceLevel.SEMANTIC.value).lower()
        try:
            equivalence = EquivalenceLevel(raw_level)
        except ValueError:
            equivalence = EquivalenceLevel.SEMANTIC
        fail_mode = str(entry.get("fail_mode") or "fail").lower()
        max_steps = entry.get("max_steps")
        if isinstance(max_steps, str) and max_steps.isdigit():
            max_steps = int(max_steps)
        elif not isinstance(max_steps, int):
            max_steps = None
        logging_root = _resolve_path(entry.get("logging_root"))
        raw_tiers = entry.get("tiers") or {}
        gate_tier = str(raw_tiers.get("gate") or entry.get("gate_tier") or "E1").strip().upper()
        trophy_value = raw_tiers.get("trophy") or entry.get("trophy_tier")
        trophy_tier = str(trophy_value).strip().upper() if trophy_value else None
        scenarios.append(
            ParityScenario(
                name=name,
                config=config,
                mode=mode,
                session=session,
                task=task,
                script=script,
                script_output=script_output,
                todo_expected=todo_expected,
                guardrails_expected=guardrails_expected,
                golden_workspace=golden_workspace,
                workspace_seed=workspace_seed,
                golden_meta=golden_meta,
                equivalence=equivalence,
                description=description,
                tags=tags,
                guard_fixture=guard_fixture,
                enabled=enabled,
                fail_mode=fail_mode,
                max_steps=max_steps,
                logging_root=logging_root,
                gate_tier=gate_tier,
                trophy_tier=trophy_tier or gate_tier,
            )
        )
    return scenarios
