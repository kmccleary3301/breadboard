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
from breadboard_engine.compaction.presets import build_recipe, load_preset_document
from breadboard_engine.compaction.primitives.accounting import OccupancyInput, normalize_usage
from breadboard_engine.compaction.primitives.triggers import TriggerInput
from breadboard_engine.compaction.state import NATIVE_MARKER_KEY
from breadboard_engine.compaction.remote.openai import OpenAIResponsesCompactionPort
from breadboard_engine.compaction.primitives.byte_estimator import response_items
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
    def __init__(self, responses: List[Any]) -> None:
        self.responses = list(responses)
        self.requests: List[SummaryRequest] = []

    def complete(self, request: SummaryRequest) -> SummaryResponse:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("summary model called more times than the oracle recorded")
        response = self.responses.pop(0)
        if isinstance(response, Mapping) and "error" in response:
            error = response["error"]
            kinds = {"RuntimeError": RuntimeError, "ValueError": ValueError}
            assert error["kind"] in kinds, f"unsupported scripted exception {error['kind']}"
            raise kinds[error["kind"]](error["message"])
        if isinstance(response, Mapping):
            return SummaryResponse(response["content"], finish_reason=response.get("finish_reason"))
        return SummaryResponse(response)


class ScriptedNative(OpenAIResponsesCompactionPort):
    """Exercise the real native replacement port with captured transport output."""

    def __init__(self, responses: List[Any]) -> None:
        self.responses = list(responses)
        super().__init__(model=TARGET.model, http_poster=self._scripted_post)

    def _scripted_post(self, url, payload, headers):
        assert url.endswith("/responses"), "captured native response requires the streaming route"
        assert payload["input"][-1] == {"type": "compaction_trigger"}
        assert self.responses, "native model called more times than the oracle recorded"
        return [
            *({"type": "response.output_item.done", "item": item} for item in self.responses.pop(0)),
            {"type": "response.completed", "response": {"usage": {}}},
        ]

def _recipe(case: Mapping[str, Any]):
    inp = case["input"]
    config = load_compaction_config({"enabled": True, "preset": case["preset"], **(inp.get("native_settings") or {})})
    if "primitive_stage" in inp:
        # Captured plugin hooks are direct component contracts, not stages in
        # the production native-compaction lifecycle.
        document = load_preset_document(case["preset"])
        document["pipeline"] = {"mode": "sequence", "stages": [inp["primitive_stage"]]}
        document["request_view"] = []
        return build_recipe(config, document)
    return build_recipe(config)


def _context(case, state: CompactionState, messages, summarizer=None) -> CompactionContext:
    inp = case["input"]
    recipe = _recipe(case)
    target = ProjectionTarget("openai", "responses", TARGET.model) if "native_responses" in inp else TARGET
    stage_ids = inp.get("stage_ids") or []
    request_pass = bool(stage_ids) and all(
        stage_id in recipe.request_view
        or recipe.pipeline.stages[stage_id].phase == "user_turn_end"
        for stage_id in stage_ids
    )
    return CompactionContext(
        messages=messages,
        state=state,
        settings=recipe.settings,
        reason="request" if request_pass else inp.get("reason") or "threshold",
        target=target,
        context_window=inp["context_window"],
        tokens_before=recipe.count(state.project(messages, target, coalesce=False)),
        summarizer=summarizer,
        max_input_tokens=inp.get("max_input_tokens"),
        max_output_tokens=inp.get("max_output_tokens"),
        last_usage=inp.get("usage"),
        native_retention=recipe.native_retention,
        remote_ports=(ScriptedNative(inp["native_responses"]),) if "native_responses" in inp else (),
        prior_compactions=inp.get("prior_compactions", 0),
        overflow_tokens=inp.get("overflow_tokens"),
        overflow_limit=inp.get("overflow_limit"),
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
            summary_messages=prior.get("summary_messages") or [{"role": "user", "content": prior["summary"]}],
            details=prior.get("details") or {},
            prefix_end=prior.get("prefix_end"),
            reset_context=prior.get("reset_context", False),
            coalesce_user=prior.get("coalesce_user", False),
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
    state = _state_from_ledger(case, messages)

    if "trigger" in expect:
        data = TriggerInput(
            OccupancyInput(state.project(messages, TARGET, coalesce=False), normalize_usage(inp.get("usage")), usage_fresh=True),
            inp["context_window"],
            inp.get("max_output_tokens"),
            reason=inp.get("reason") or "threshold",
            max_input_tokens=inp.get("max_input_tokens"),
            blocked=inp.get("trigger_blocked", False),
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

    summaries = ScriptedSummaries(inp.get("summary_responses") or [])
    context = _context(case, state, messages, summaries)
    if "selection" in expect:
        order = inp.get("stage_ids") or inp.get("pipeline_order") or inp.get("stages") or recipe.order or recipe.pipeline.order
        stage = recipe.pipeline.stages[inp["stage"]] if "stage" in inp else next(
            recipe.pipeline.stages[name] for name in order
            if isinstance(recipe.pipeline.stages[name].step, ComposedStep)
            and (recipe.pipeline.stages[name].reasons is None or context.reason in recipe.pipeline.stages[name].reasons)
        )
        assert isinstance(stage.step, ComposedStep), "selection cases need a composed stage"
        if "message_token_estimates" in inp:
            assert [stage.step.selector.count([message]) for message in messages] == inp["message_token_estimates"]
        selection = stage.step.selector.select(context)

    if "selection" in expect:
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
            target_tokens=recipe.target_tokens(context.reason, inp["context_window"], inp.get("max_output_tokens"), inp.get("max_input_tokens")),
            order=inp.get("stage_ids", inp.get("pipeline_order", inp.get("stages", recipe.order))),
        )
    for port in context.remote_ports:
        assert not port.responses, "oracle recorded native calls this preset did not make"
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
        projected = state.project(messages, context.target)
        if inp.get("projection") == "responses":
            projected = [
                item for message in projected
                for item in (message[NATIVE_MARKER_KEY]["items"] if NATIVE_MARKER_KEY in message else response_items(message))
            ]
        assert projected == expect["projected_view"]


def context_reason(case: Mapping[str, Any]) -> str:
    return case["input"].get("reason") or "threshold"
