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
CHECKED = {"trigger", "selection", "summary_requests", "projected_view", "summary", "details"}


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
    return CompactionContext(
        messages=messages,
        state=state,
        settings=recipe.settings,
        reason=inp.get("reason") or "threshold",
        target=TARGET,
        context_window=inp["context_window"],
        tokens_before=recipe.count(state.project(messages, TARGET)),
        summarizer=summarizer,
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
        )
        assert len(recipe.triggers) == 1, "trigger cases assume a single-trigger preset"
        assert recipe.triggers[0].evaluate(data, messages).to_dict() == expect["trigger"]

    compaction_keys = CHECKED - {"trigger"}
    if not compaction_keys & set(expect):
        return

    state = _state_from_ledger(case, messages)
    summaries = ScriptedSummaries(inp.get("summary_responses") or [])
    context = _context(case, state, messages, summaries)
    stage = recipe.pipeline.stages[recipe.pipeline.order[0]]
    assert isinstance(stage.step, ComposedStep), "selection cases need a composed first stage"
    if "selection" in expect:
        selection = stage.step.selector.select(context)
        got_selection = {
            k: list(v) if isinstance(v, tuple) else v for k, v in ((k, getattr(selection, k)) for k in expect["selection"])
        }
        assert got_selection == expect["selection"]

    outcome = recipe.pipeline.run(
        context,
        target_tokens=recipe.target_tokens(context.reason, inp["context_window"]),
    )
    assert outcome.status == "committed", outcome.stages
    assert not summaries.responses, "oracle recorded summary calls this preset did not make"
    record = outcome.records[-1]

    if "summary_requests" in expect:
        got = [
            {"system": r.system, "messages": [dict(m) for m in r.messages], "max_tokens": r.max_tokens}
            for r in summaries.requests
        ]
        want = [{k: r[k] for k in ("system", "messages", "max_tokens")} for r in expect["summary_requests"]]
        assert got == want
    if "summary" in expect:
        assert record.summary == expect["summary"]
    if "details" in expect:
        assert dict(record.details) == expect["details"]
    if "projected_view" in expect:
        assert state.project(messages, TARGET) == expect["projected_view"]
