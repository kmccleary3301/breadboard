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
from breadboard_engine.compaction.pipeline import CompactionOutcome, ComposedStep, StageResult
from breadboard_engine.compaction.presets import build_recipe
from breadboard_engine.compaction.primitives.accounting import OccupancyInput, normalize_usage
from breadboard_engine.compaction.primitives.triggers import TriggerInput
from breadboard_engine.compaction.primitives.reducers import Reduction
from breadboard_engine.compaction.primitives.selectors import Selection

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
        response = self.responses.pop(0)
        if isinstance(response, Mapping):
            return SummaryResponse(response["content"], finish_reason=response.get("finish_reason"))
        return SummaryResponse(response)


def _recipe(case: Mapping[str, Any]):
    inp = case["input"]
    config = load_compaction_config({"enabled": True, "preset": case["preset"], **(inp.get("native_settings") or {})})
    return build_recipe(config)


def _context(case, state: CompactionState, messages, summarizer=None) -> CompactionContext:
    inp = case["input"]
    recipe = _recipe(case)
    estimates = inp.get("message_token_estimates")
    if estimates is not None:
        assert len(estimates) == len(messages)
    token_counts = {id(m): tokens for m, tokens in zip(messages, estimates or [])}
    return CompactionContext(
        messages=messages,
        state=state,
        settings=recipe.settings,
        reason=inp.get("reason") or "threshold",
        target=TARGET,
        context_window=inp["context_window"],
        tokens_before=recipe.count(state.project(messages, TARGET)),
        summarizer=summarizer,
        prior_compactions=inp.get("prior_compactions", 0),
        token_estimator=(lambda message: token_counts[id(message)]) if estimates is not None else None,
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


# Helper captures declare the stage role, production method, argument object,
# and whether that method consumes the pass context. There are no preset names
# or expected-output-derived arguments in this dispatch.
_COMPONENT_METHODS = {
    "quality_audit": ("reducer", "audit_summary", "audit_input", False),
    "summary_request": ("reducer", "reduce_text", "summary_input", True),
    "placement": ("placement", "place", "placement_input", True),
    "reduction": ("reducer", "reduce", "reduction_input", True),
}


def _run_component(component, inp, recipe, context):
    owner, method, input_key, uses_context = _COMPONENT_METHODS[component]
    step = recipe.pipeline.stages[inp["stage"]].step
    assert isinstance(step, ComposedStep)
    arguments = dict(inp[input_key])
    if "selection" in arguments:
        arguments["selection"] = Selection(**arguments["selection"])
    if owner == "placement":
        arguments["reduction"] = Reduction(summary=arguments.pop("summary"),
                                           details=arguments.pop("details", {}))
    if uses_context:
        arguments["context"] = context
    records, stages, result = (), (), None
    try:
        result = getattr(getattr(step, owner), method)(**arguments)
        if "selection" in arguments:
            reduction = result if isinstance(result, Reduction) else arguments["reduction"]
            placed = () if isinstance(result, Reduction) else result
            record = step.record_output(context, arguments["selection"], reduction, placed)
            context.state.append(record, context.messages)
            records = (record,)
    except Exception as exc:
        stages = (StageResult(inp["stage"], "failed", f"{getattr(exc, 'kind', type(exc).__name__)}: {exc}"),)
    after = recipe.count(context.projected())
    target = recipe.target_tokens(context.reason, context.context_window)
    return CompactionOutcome(records, stages, context.tokens_before, after, target, bool(records) and after <= target), result


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
            blocked=inp.get("trigger_blocked", False),
        )
        pressure = recipe.pressure(data, messages)
        assert pressure is not None, "trigger case for a preset without triggers"
        assert pressure.to_dict() == expect["trigger"]

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
        stage = recipe.pipeline.stages[inp.get("stage", recipe.pipeline.order[0])]
        assert isinstance(stage.step, ComposedStep), "selection cases need a composed first stage"
        selection = stage.step.selector.select(context)
        got_selection = {
            k: list(v) if isinstance(v, tuple) else v for k, v in ((k, getattr(selection, k)) for k in expect["selection"])
        }
        assert got_selection == expect["selection"]
        if set(expect) == {"selection"}:
            return

    component = inp.get("component")
    component_result = None
    if component is not None:
        assert component in _COMPONENT_METHODS, f"unknown helper component {component!r}"
        outcome, component_result = _run_component(component, inp, recipe, context)
    else:
        outcome = recipe.pipeline.run(
            context,
            target_tokens=recipe.target_tokens(context.reason, inp["context_window"]),
            order=inp.get("stages"),
        )
    assert not summaries.responses, "oracle recorded summary calls this preset did not make"

    if "failure" in expect:
        failure = expect["failure"]
        assert not outcome.records, outcome.stages
        details = [s.detail for s in outcome.stages if s.status == "failed"]
        assert f"{failure['kind']}: {failure['message']}" in details, outcome.stages
    if "summary_requests" in expect:
        got = [
            {"system": r.system, "messages": [dict(m) for m in r.messages], "max_tokens": r.max_tokens}
            for r in summaries.requests
        ]
        want = [{k: r[k] for k in ("system", "messages", "max_tokens")} for r in expect["summary_requests"]]
        assert got == want
    if "summary" in expect or "details" in expect:
        boundaries = [r for r in outcome.records if r.is_boundary]
        assert boundaries or component_result is not None, outcome.stages
        result = boundaries[-1] if boundaries else component_result
        if "summary" in expect:
            assert result.summary == expect["summary"]
        if "details" in expect:
            details = result if isinstance(result, Mapping) else result.details
            assert dict(details) == expect["details"]
    if "edits" in expect:
        got_edits = [{"index": e.index, "message": dict(e.message)} for r in outcome.records for e in r.edits]
        assert got_edits == expect["edits"]
    if "projected_view" in expect:
        assert state.project(messages, TARGET) == expect["projected_view"]


def context_reason(case: Mapping[str, Any]) -> str:
    return case["input"].get("reason") or "threshold"
