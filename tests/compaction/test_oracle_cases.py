"""Differential oracle cases: each preset against its harness's captured behavior.

Cases live in ``tests/compaction/oracles/<preset>/<case>.json``
(``bb.compaction_oracle_case.v1``), captured by ``scripts/compaction_oracles``.
Every ``expect`` key a case carries is checked; a key the runner cannot check
fails the case. A preset directory without a packaged preset yet is skipped
by name, so the gap stays visible in the test report.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping

import pytest

from breadboard_engine.compaction import (
    CompactionContext,
    CompactionState,
    ProjectionTarget,
    SummaryRequest,
    SummaryResponse,
    available_presets,
    load_compaction_config,
)
from breadboard_engine.compaction.pipeline import ComposedStep
from breadboard_engine.compaction.presets import build_recipe
from breadboard_engine.compaction.primitives.accounting import OccupancyInput, normalize_usage
from breadboard_engine.compaction.primitives.triggers import TriggerInput

ORACLES = Path(__file__).parent / "oracles"
TARGET = ProjectionTarget("oracle", "oracle", "oracle-model")
CHECKED = {"trigger", "selection", "summary_requests", "projected_view", "summary", "details", "edits", "failure"}


def _cases() -> List[Any]:
    params = []
    presets = set(available_presets())
    for case_file in sorted(ORACLES.glob("*/*.json")):
        preset = case_file.parent.name
        marks = [] if preset in presets else [pytest.mark.skip(reason=f"preset {preset} is not packaged yet")]
        params.append(pytest.param(case_file, id=f"{preset}/{case_file.stem}", marks=marks))
    return params


class ScriptedSummaries:
    def __init__(self, responses: List[str]) -> None:
        self.responses = list(responses)
        self.requests: List[SummaryRequest] = []

    def complete(self, request: SummaryRequest) -> SummaryResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("summary model called more times than the oracle recorded")
        return SummaryResponse(self.responses.pop(0))


def _recipe(case: Mapping[str, Any]):
    inp = case["input"]
    config = load_compaction_config({"enabled": True, "preset": case["preset"], **(inp.get("native_settings") or {})})
    return build_recipe(config)


def _context(case, state: CompactionState, messages, summarizer=None) -> CompactionContext:
    inp = case["input"]
    recipe = _recipe(case)
    usage = normalize_usage(inp.get("usage"))
    provider_tokens = (usage.total or usage.input + usage.output + usage.cache_read + usage.cache_write) if usage else None
    return CompactionContext(
        messages=messages,
        state=state,
        settings=recipe.settings,
        reason=inp.get("reason") or "threshold",
        target=TARGET,
        context_window=inp["context_window"],
        tokens_before=recipe.count(state.project(messages, TARGET)),
        summarizer=summarizer,
        max_output_tokens=inp.get("max_output_tokens"),
        max_input_tokens=inp.get("max_input_tokens"),
        provider_tokens=provider_tokens,
    )


def _state_from_ledger(case, messages) -> CompactionState:
    state = CompactionState()
    for prior in case["input"].get("ledger") or []:
        prefix = messages[: prior["history_length"]]
        ctx = _context(case, state, prefix)
        record = ctx.new_record(
            method="oracle_ledger",
            first_kept_index=prior["first_kept_index"],
            summary=prior["summary"],
            summary_messages=[{"role": "user", "content": prior["summary"]}],
            details=prior.get("details") or {},
        )
        state.append(record, prefix)
    return state


_CASE_KEYS = {"schema", "preset", "case", "source", "capture", "input", "expect"}
_EXPECT_KEYS = CHECKED | {"edits", "failure"}


@pytest.mark.parametrize(
    "case_file", [pytest.param(p, id=f"{p.parent.name}/{p.stem}") for p in sorted(ORACLES.glob("*/*.json"))]
)
def test_oracle_case_is_well_formed(case_file: Path) -> None:
    """Every captured case, packaged preset or not, states provenance and at least one expectation."""
    case = json.loads(case_file.read_text(encoding="utf-8"))
    assert set(case) == _CASE_KEYS
    assert case["schema"] == "bb.compaction_oracle_case.v1"
    assert (case["preset"], case["case"]) == (case_file.parent.name, case_file.stem)
    assert case["capture"]["kind"] in {"executed", "source_derived"}
    assert case["capture"]["script"].startswith("scripts/compaction_oracles/")
    assert case["source"].get("commit") or case["source"].get("sha256") or case["source"].get("package_sha256")
    assert case["expect"] and set(case["expect"]) <= _EXPECT_KEYS, sorted(case["expect"])
    assert "/tmp/" not in case_file.read_text(encoding="utf-8").replace("/tmp/session/", "")


@pytest.mark.parametrize("case_file", _cases())
def test_oracle_case(case_file: Path) -> None:
    case = json.loads(case_file.read_text(encoding="utf-8"))
    assert case["schema"] == "bb.compaction_oracle_case.v1"
    expect: Dict[str, Any] = case["expect"]
    unchecked = sorted(set(expect) - CHECKED)
    assert not unchecked, f"runner cannot check expect keys {unchecked}"
    inp = case["input"]
    messages = inp["messages"]
    recipe = _recipe(case)

    if "trigger" in expect:
        data = TriggerInput(
            OccupancyInput(messages, normalize_usage(inp.get("usage")), usage_fresh=True),
            inp["context_window"],
            inp.get("max_output_tokens"),
            inp.get("max_input_tokens"),
            inp.get("reason") or "threshold",
        )
        pressure = recipe.pressure(data, messages)
        assert pressure is not None, "trigger case for a preset without triggers"
        actual_pressure = pressure.to_dict()
        assert set(expect["trigger"]) <= set(actual_pressure), "unknown trigger expectation keys"
        assert {key: actual_pressure[key] for key in expect["trigger"]} == expect["trigger"]

    compaction_keys = _EXPECT_KEYS - {"trigger"}
    if not compaction_keys & set(expect):
        return

    if "failure" in expect and context_reason(case) == "overflow" and recipe.overflow_policy == "terminal":
        # The harness ends the run on overflow; BreadBoard re-raises the provider error unchanged.
        assert set(expect) <= {"trigger", "failure"}, "a terminal-overflow case cannot expect a compaction result"
        return

    state = _state_from_ledger(case, messages)
    summaries = ScriptedSummaries(inp.get("summary_responses") or [])
    context = _context(case, state, messages, summaries)
    if "selection" in expect:
        order = inp.get("stage_ids") or recipe.pipeline.order
        boundary_selection = bool(set(expect["selection"]) - {"targets"})
        stage = next(recipe.pipeline.stages[name] for name in order
                     if isinstance(recipe.pipeline.stages[name].step, ComposedStep)
                     and (recipe.pipeline.stages[name].step.placement is not None) == boundary_selection)
        selection = stage.step.selector.select(context)
        got_selection = {
            k: list(v) if isinstance(v, tuple) else v for k, v in ((k, getattr(selection, k)) for k in expect["selection"])
        }
        assert got_selection == expect["selection"]

    outcome = recipe.pipeline.run(
        context,
        target_tokens=recipe.target_tokens(context.reason, inp["context_window"],
                                          inp.get("max_output_tokens"), inp.get("max_input_tokens")),
        order=inp.get("stage_ids"),
    )
    assert not summaries.responses, "oracle recorded summary calls this preset did not make"

    if "failure" in expect:
        failure = expect["failure"]
        assert not outcome.records, outcome.stages
        details = [s.detail for s in outcome.stages if s.status == "failed"]
        assert f"{failure['kind']}: {failure['message']}" in details, outcome.stages
    if "summary_requests" in expect:
        fields = {"system", "messages", "max_tokens", "tools"}
        assert len(summaries.requests) == len(expect["summary_requests"])
        for request, expected in zip(summaries.requests, expect["summary_requests"]):
            assert set(expected) <= fields, f"unchecked summary request keys {set(expected) - fields}"
            actual = {"system": request.system, "messages": [dict(m) for m in request.messages],
                      "max_tokens": request.max_tokens, "tools": list(request.tools)}
            assert {key: actual[key] for key in expected} == expected
    if "summary" in expect or "details" in expect:
        boundaries = [r for r in outcome.records if r.is_boundary]
        assert boundaries, outcome.stages
        if "summary" in expect:
            assert boundaries[-1].summary == expect["summary"]
        if "details" in expect:
            assert dict(boundaries[-1].details) == expect["details"]
    if "edits" in expect:
        got_edits = [{"index": e.index, "message": dict(e.message)} for r in outcome.records for e in r.edits]
        assert got_edits == expect["edits"]
    if "projected_view" in expect:
        assert state.project(messages, TARGET) == expect["projected_view"]


def context_reason(case: Mapping[str, Any]) -> str:
    return case["input"].get("reason") or "threshold"
