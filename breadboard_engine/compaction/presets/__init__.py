"""Compaction presets: packaged recipes that clone one harness's compaction.

A preset is YAML at ``presets/<harness>@<version>.yaml`` (schema
``bb.compaction_preset.v1``, versioned by this loader). It names primitive
kinds and parameters; nothing in it is code. The agent config picks a preset
and sets that harness's own setting names:

.. code-block:: yaml

    compaction:
      enabled: true
      preset: pi@0.73.1
      reserveTokens: 20000      # Pi's own key, mapped by the preset
      overflow_policy: terminal  # BreadBoard key, valid with every preset

``native_settings.adapter`` decides how the non-BreadBoard keys are read:

``paths``
    each harness key writes its value into the listed recipe paths
    (``pipeline.stages.0.select.budget``); unknown keys raise and list the
    accepted names.
``omp_settings``
    the whole block goes to :func:`settings_from_config`, OMP's own parser
    (camelCase and snake_case, ``strategy``/``remoteEnabled`` migration);
    recipe values may read it through ``{setting: <field>}``.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
import importlib.resources
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

import yaml

from ..handoff import HandoffCompaction
from ..methods import CompactionMethod
from ..params import BuildEnv, Params, PresetError
from ..pipeline import MODES, REASONS, Pipeline, build_stage
from ..primitives.accounting import build_estimator
from ..primitives.triggers import Pressure, TriggerInput, build_limit, build_trigger
from ..remote import RemoteCompaction
from ..settings import CompactionSettings, settings_from_config
from ..shake import ShakeCompaction
from ..snapcompact import SnapcompactCompaction

PRESET_SCHEMA = "bb.compaction_preset.v1"
DEFAULT_PRESET = "omp@18.4.5"
REQUEST_VIEW_STEPS = ("omp_prune", "omp_inline_snapcompact", "pipeline_edits")
CLAIMS = ("executed_oracle", "source_golden")

ALGORITHMS: Dict[str, Callable[[], CompactionMethod]] = {
    "omp_remote": RemoteCompaction,
    "omp_snapcompact": SnapcompactCompaction,
    "omp_handoff": HandoffCompaction,
    "omp_shake": ShakeCompaction,
}

# BreadBoard-level keys: valid with every preset, in either spelling.
_BB_KEYS = {
    "enabled": "enabled",
    "preset": "preset",
    "overflow_policy": "overflow_policy",
    "overflowPolicy": "overflow_policy",
    "context_window": "context_window",
    "contextWindow": "context_window",
    "summary_model": "summary_model",
    "summaryModel": "summary_model",
    "max_passes_per_turn": "max_passes_per_turn",
    "maxPassesPerTurn": "max_passes_per_turn",
}


def _presets_root() -> Any:
    return importlib.resources.files(__name__)


def available_presets() -> Tuple[str, ...]:
    return tuple(
        sorted(entry.name[: -len(".yaml")] for entry in _presets_root().iterdir() if entry.name.endswith(".yaml"))
    )


def load_preset_document(preset_id: str) -> Dict[str, Any]:
    entry = _presets_root().joinpath(f"{preset_id}.yaml")
    if not entry.is_file():
        raise PresetError(f"unknown compaction preset {preset_id!r}; available: {list(available_presets())}")
    doc = yaml.safe_load(entry.read_text(encoding="utf-8"))
    if not isinstance(doc, Mapping) or doc.get("schema") != PRESET_SCHEMA:
        raise PresetError(f"preset {preset_id!r} must declare schema {PRESET_SCHEMA}")
    if doc.get("id") != preset_id:
        raise PresetError(f"preset file {preset_id}.yaml declares id {doc.get('id')!r}")
    return dict(doc)


# --------------------------------------------------------------------------
# Agent config
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class CompactionConfig:
    """Parsed ``compaction:`` block. ``settings`` is the native settings object."""

    enabled: bool
    preset: str
    settings: CompactionSettings
    native: Mapping[str, Any]
    """Harness-native keys as written (``paths`` adapter only)."""
    max_passes_per_turn: Optional[int]
    """Explicit BreadBoard override; ``None`` uses the preset's overflow budget."""
    overflow_policy: Optional[str]
    """Explicit BreadBoard override; ``None`` uses the preset's ``overflow.policy``."""


def load_compaction_config(raw: Any) -> CompactionConfig:
    """Parse and fully validate an agent config ``compaction`` block.

    ``None``/absent means disabled; ``true`` enables the default preset.
    The preset is built here, so a bad key or value fails at config load.
    """
    if raw is None or raw is False:
        return CompactionConfig(False, DEFAULT_PRESET, CompactionSettings(), {}, None, None)
    if raw is True:
        raw = {"enabled": True}
    if not isinstance(raw, Mapping):
        raise ValueError("compaction config must be a mapping")
    preset_id = raw.get("preset", DEFAULT_PRESET)
    if not isinstance(preset_id, str):
        raise ValueError("compaction.preset must be a string")
    doc = load_preset_document(preset_id)
    adapter = (doc.get("native_settings") or {}).get("adapter")
    rest = {k: v for k, v in raw.items() if k != "preset"}
    if adapter == "omp_settings":
        settings = settings_from_config(rest)
        config = CompactionConfig(
            settings.enabled,
            preset_id,
            settings,
            {},
            settings.max_passes_per_turn if _has_any(rest, "max_passes_per_turn", "maxPassesPerTurn") else None,
            settings.overflow_policy if _has_any(rest, "overflow_policy", "overflowPolicy") else None,
        )
    elif adapter == "paths":
        config = _paths_config(preset_id, doc, rest)
    else:
        raise PresetError(f"preset {preset_id!r} native_settings.adapter must be 'paths' or 'omp_settings'")
    build_recipe(config, doc)
    return config


def _has_any(raw: Mapping[str, Any], *keys: str) -> bool:
    return any(key in raw for key in keys)


def _paths_config(preset_id: str, doc: Mapping[str, Any], raw: Mapping[str, Any]) -> CompactionConfig:
    keys = (doc.get("native_settings") or {}).get("keys") or {}
    bb: Dict[str, Any] = {}
    native: Dict[str, Any] = {}
    seen: Dict[str, str] = {}
    for key, value in raw.items():
        if key in _BB_KEYS:
            attr = _BB_KEYS[key]
            if attr in seen:
                raise ValueError(f"compaction keys {seen[attr]!r} and {key!r} conflict")
            seen[attr] = key
            bb[attr] = value
        elif key in keys:
            native[key] = value
        else:
            accepted = sorted({*keys, *(k for k in _BB_KEYS if k != "preset")})
            raise ValueError(f"unknown compaction key {key!r} for preset {preset_id}; accepted keys are {accepted}")
    settings_raw = {k: v for k, v in bb.items() if k != "preset"}
    # Only BreadBoard-level keys reach OMP's parser here; it validates their types.
    settings = settings_from_config(settings_raw)
    max_passes = bb.get("max_passes_per_turn")
    policy = settings.overflow_policy if "overflow_policy" in bb else None
    return CompactionConfig(settings.enabled, preset_id, settings, native, max_passes, policy)


# --------------------------------------------------------------------------
# Recipe
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Recipe:
    preset_id: str
    claim: str
    settings: CompactionSettings
    count: Callable[[Any], int]
    triggers: Tuple[Any, ...]
    targets: Mapping[str, Any]
    pipeline: Pipeline
    order: Optional[Tuple[str, ...]]
    """Stage order override (OMP ``methodOrder``); ``None`` runs the preset order."""
    max_attempts_per_turn: int
    overflow_policy: str
    """``compact`` (compact and retry) or ``terminal`` (the overflow error ends the run)."""
    request_view: Tuple[str, ...]

    @property
    def active(self) -> bool:
        stage_order = self.order if self.order is not None else self.pipeline.order
        return self.settings.enabled and bool(stage_order)

    def target_tokens(self, reason: str, context_window: int, max_output: Optional[int] = None) -> int:
        return self.targets[reason].tokens(context_window, max_output)

    def pressure(self, data: TriggerInput, history: Any) -> Optional[Pressure]:
        """The first firing trigger's decision; else the first trigger's; ``None`` without triggers."""
        decisions = [trigger.evaluate(data, history) for trigger in self.triggers]
        return next((d for d in decisions if d.fires), decisions[0] if decisions else None)


def _set_path(doc: Dict[str, Any], path: str, value: Any, where: str) -> None:
    node: Any = doc
    parts = path.split(".")
    for part in parts[:-1]:
        node = node[int(part)] if isinstance(node, list) else node.get(part)
        if node is None:
            raise PresetError(f"{where}: path {path!r} does not exist in the preset")
    last = parts[-1]
    if isinstance(node, list):
        node[int(last)] = value
    elif isinstance(node, dict) and last in node:
        node[last] = value
    else:
        raise PresetError(f"{where}: path {path!r} does not exist in the preset")


def build_recipe(config: CompactionConfig, doc: Optional[Mapping[str, Any]] = None) -> Recipe:
    raw = copy.deepcopy(dict(doc if doc is not None else load_preset_document(config.preset)))
    native_keys = (raw.get("native_settings") or {}).get("keys") or {}
    for key, value in config.native.items():
        paths = native_keys[key]
        for path in paths if isinstance(paths, list) else [paths]:
            _set_path(raw, path, value, f"preset {config.preset} native_settings.keys.{key}")
    prompt_dir = _presets_root().joinpath("prompts", config.preset)
    env = BuildEnv(config.settings, prompt_dir if prompt_dir.is_dir() else None)
    params = Params(raw, f"preset {config.preset}", env)
    params.str("schema")
    params.str("id")
    params.mapping("source")
    claim = params.choice("claim", CLAIMS)
    params.mapping("native_settings")
    count = build_estimator(params.str("estimator"))

    triggers = tuple(
        build_trigger(params.child(item, f"triggers[{i}]")) for i, item in enumerate(params.list("triggers"))
    )
    target_params = params.child(params.mapping("target"), "target")
    targets = {reason: build_limit(target_params.child(target_params.mapping(reason), reason)) for reason in REASONS}
    target_params.done()

    overflow = params.child(params.mapping("overflow"), "overflow")
    preset_attempts = overflow.int("max_attempts_per_turn", minimum=0)
    preset_policy = overflow.choice("policy", ("compact", "terminal"))
    overflow.done()

    request_view = tuple(params.list("request_view"))
    unknown_steps = [s for s in request_view if s not in REQUEST_VIEW_STEPS]
    if unknown_steps:
        raise PresetError(f"preset {config.preset} request_view has unknown steps {unknown_steps}")

    pipe = params.child(params.mapping("pipeline"), "pipeline")
    mode = pipe.choice("mode", MODES)
    order_source = pipe.choice("order", ("preset", "omp_method_order"), "preset")
    stages = [
        build_stage(pipe.child(item, f"stages[{i}]"), ALGORITHMS) for i, item in enumerate(pipe.list("stages"))
    ]
    if "pipeline_edits" in request_view and any(
        getattr(stage.step.selector, "kind", None) != "visible_tool_outputs" for stage in stages
    ):
        raise PresetError("pipeline_edits requires edit-only selectors")
    pipe.done()
    params.done()
    ids = [s.id for s in stages]
    if len(set(ids)) != len(ids):
        raise PresetError(f"preset {config.preset} has duplicate stage ids {ids}")

    order = None
    if order_source == "omp_method_order":
        order = config.settings.method_order
    attempts = config.max_passes_per_turn if config.max_passes_per_turn is not None else preset_attempts
    return Recipe(
        preset_id=config.preset,
        claim=claim,
        settings=config.settings,
        count=count,
        triggers=triggers,
        targets=targets,
        pipeline=Pipeline(stages, mode, count),
        order=order,
        max_attempts_per_turn=attempts,
        overflow_policy=config.overflow_policy or preset_policy,
        request_view=request_view,
    )
