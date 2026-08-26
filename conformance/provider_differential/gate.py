"""Fail-closed comparison engine for the F6 provider differential matrix."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any, Mapping, Sequence
from .auth_role_rows import (
    AUTH_ROLE_PUBLIC_API_CALLS,
    AUTH_ROLE_REQUIRED_API_CALLS,
    auth_role_observation_sha256,
    project_auth_role_observation,
)

from .contracts import (
    ALL_ROW_IDS,
    ARTIFACT_ROW_IDS,
    AUTH_ROLE_ROW_IDS,
    ORACLE_IDENTITY,
    PROVIDER_ROW_IDS,
    ContractError,
    canonical_json,
    sha256_bytes,
    validate_observation,
    validate_row_set,
    validate_semantic_trace,
)

MATRIX_ID = "f6-supported-provider-semantics-v1"
MATRIX_SCHEMA = "bb.provider_differential_matrix.v1"
ORACLE_SCHEMA = "bb.f6.omp_oracle.v1"
# Independently pin the generated fixture; row-local hashes alone are mutable.
ORACLE_FIXTURE_SHA256 = (
    "sha256:8d55c5adac18dacb90d4fce72d52c57745be16d91f8ad4284537c80153788faf"
)

INTENTIONAL_DIVERGENCES = frozenset(
    {
        "role.lock_secret_rotation_restart",
        "role.auth_policy_no_fallback",
        "role.cross_provider_default_forbidden",
    }
)
_SOURCE_ROW_IDS = PROVIDER_ROW_IDS + AUTH_ROLE_ROW_IDS
_EXPECTED_RUNTIME = {
    "codex": "codex_app_server",
    "openai": "openai_chat",
    "anthropic": "anthropic_messages",
    "openrouter": "openrouter_chat",
}
_EXPECTED_AUTH = {
    "codex": ("provider", ["openai-codex"], ["provider_managed"]),
    "openai": ("broker", [], ["api_key"]),
    "anthropic": ("broker", [], ["api_key", "oauth2"]),
    "openrouter": ("broker", [], ["api_key"]),
}


class DifferentialError(ContractError):
    """The matrix, oracle, observation, or comparison is invalid."""


class _PredicateFailure(DifferentialError):
    pass


@dataclass(frozen=True, slots=True)
class Comparison:
    row_id: str
    classification: str
    detail: str


@dataclass(frozen=True, slots=True)
class Evaluation:
    comparisons: tuple[Comparison, ...]

    @property
    def claimable(self) -> bool:
        return all(
            row.classification in {"match", "intentional_divergence"}
            for row in self.comparisons
        )

    @property
    def counts(self) -> dict[str, int]:
        result = {
            "match": 0,
            "intentional_divergence": 0,
            "BreadBoard_defect": 0,
            "unverified": 0,
        }
        for row in self.comparisons:
            result[row.classification] += 1
        return result


def _fail(message: str) -> None:
    raise _PredicateFailure(message)


def _require(condition: bool, message: str) -> None:
    if not condition:
        _fail(message)


def _mapping(value: Any, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(f"{label} must be an object")
    return value


def _sequence(value: Any, label: str) -> Sequence[Any]:
    if not isinstance(value, (list, tuple)):
        _fail(f"{label} must be an array")
    return value


def _oracle_hash(value: Any) -> str:
    return sha256_bytes((canonical_json(value) + "\n").encode("utf-8"))


def load_matrix(path: Path) -> Mapping[str, Any]:
    try:
        matrix = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DifferentialError(f"cannot load matrix {path}: {exc}") from exc
    if not isinstance(matrix, Mapping):
        raise DifferentialError("provider differential matrix must be an object")
    expected_keys = {
        "schema_version",
        "matrix_id",
        "oracle_identity",
        "required_row_count",
        "rows",
    }
    if set(matrix) != expected_keys:
        raise DifferentialError("provider differential matrix has unexpected fields")
    if matrix["schema_version"] != MATRIX_SCHEMA or matrix["matrix_id"] != MATRIX_ID:
        raise DifferentialError("provider differential matrix identity mismatch")
    if (
        dict(_mapping(matrix["oracle_identity"], "matrix oracle identity"))
        != ORACLE_IDENTITY
    ):
        raise DifferentialError("matrix oracle identity does not match F1")
    if matrix["required_row_count"] != len(ALL_ROW_IDS):
        raise DifferentialError("matrix row count is not exact")
    rows = _sequence(matrix["rows"], "matrix rows")
    validate_row_set([_mapping(row, "matrix row").get("row_id") for row in rows])
    expected_comparators = {
        "catalog_model_route": "catalog_route_contract.v1",
        "request_ir": "request_semantic_contract.v1",
        "text_stream": "stream_semantic_contract.v1",
        "tool_stream": "tool_semantic_contract.v1",
        "usage_finish": "usage_finish_contract.v1",
        "error_terminal": "error_terminal_contract.v1",
        "cancel_terminal": "cancel_terminal_contract.v1",
    }
    for row_value in rows:
        row = _mapping(row_value, "matrix row")
        row_id = str(row["row_id"])
        required = {"row_id", "subject", "seam", "claim", "comparator", "required"}
        allowed = required | {"allowed_divergence_ref"}
        if not required.issubset(row) or not set(row).issubset(allowed):
            raise DifferentialError(f"matrix row {row_id!r} has invalid fields")
        if row["required"] is not True:
            raise DifferentialError(f"matrix row {row_id!r} is not required")
        expected = (
            "auth_contract.v1"
            if row_id.startswith("auth.")
            else "role_contract.v1"
            if row_id.startswith("role.")
            else "artifact_contract.v1"
            if row_id.startswith("artifact.")
            else expected_comparators[row_id.split(".", 1)[1]]
        )
        if row["comparator"] != expected:
            raise DifferentialError(f"matrix row {row_id!r} has wrong comparator")
        divergence = row.get("allowed_divergence_ref")
        if divergence is not None and (
            not isinstance(divergence, str) or not divergence
        ):
            raise DifferentialError(
                f"matrix row {row_id!r} has invalid divergence reference"
            )
        if row_id in INTENTIONAL_DIVERGENCES and not divergence:
            raise DifferentialError(
                f"intentional row {row_id!r} lacks its F5 reference"
            )
    return matrix


def load_oracle(path: Path, runner_path: Path) -> Mapping[str, Any]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DifferentialError(f"cannot load oracle fixture {path}: {exc}") from exc
    if (
        ORACLE_FIXTURE_SHA256
        and sha256_bytes(raw.encode("utf-8")) != ORACLE_FIXTURE_SHA256
    ):
        raise DifferentialError("oracle fixture pinned digest mismatch")
    try:
        fixture = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise DifferentialError(f"cannot load oracle fixture {path}: {exc}") from exc
    if not isinstance(fixture, Mapping):
        raise DifferentialError("oracle fixture must be an object")
    if canonical_json(fixture) + "\n" != raw:
        raise DifferentialError("oracle fixture is not canonical JSON")
    if fixture.get("schema_version") != ORACLE_SCHEMA:
        raise DifferentialError("oracle schema version mismatch")
    oracle = _mapping(fixture.get("oracle"), "oracle identity")
    if (
        oracle.get("head") != ORACLE_IDENTITY["commit"]
        or oracle.get("tree") != ORACLE_IDENTITY["tree"]
    ):
        raise DifferentialError("oracle commit/tree mismatch")
    blobs = _mapping(oracle.get("source_blobs"), "oracle source blobs")
    if not blobs or any(
        not isinstance(path_value, str) or not isinstance(blob, str) or len(blob) != 40
        for path_value, blob in blobs.items()
    ):
        raise DifferentialError("oracle source blob inventory is invalid")
    try:
        runner_hash = (
            "sha256:"
            + __import__("hashlib").sha256(runner_path.read_bytes()).hexdigest()
        )
    except OSError as exc:
        raise DifferentialError(f"cannot hash oracle runner: {exc}") from exc
    if fixture.get("runner_sha256") != runner_hash:
        raise DifferentialError("oracle fixture runner hash mismatch")
    rows = _sequence(fixture.get("rows"), "oracle rows")
    ids = [str(_mapping(row, "oracle row").get("id")) for row in rows]
    validate_row_set(ids, expected=_SOURCE_ROW_IDS)
    canaries = (
        "F6_DUMMY_CANARY_CREDENTIAL_7c2f8e1a",
        "F6_ARTIFACT_SECRET_CANARY_7b61d29f",
    )
    if any(canary in raw for canary in canaries):
        raise DifferentialError("oracle fixture contains a secret canary")
    for row_value in rows:
        row = _mapping(row_value, "oracle row")
        row_id = str(row["id"])
        if row.get("input_sha256") != _oracle_hash(row.get("input")):
            raise DifferentialError(f"oracle input hash mismatch for {row_id}")
        if row.get("output_sha256") != _oracle_hash(row.get("output")):
            raise DifferentialError(f"oracle output hash mismatch for {row_id}")
    return fixture


def _event_kinds(events_value: Any, label: str) -> list[str]:
    events = _sequence(events_value, label)
    kinds: list[str] = []
    text = thinking = False
    tools: set[str] = set()
    for index, event_value in enumerate(events):
        event = _mapping(event_value, f"{label}[{index}]")
        kind = event.get("kind")
        _require(isinstance(kind, str), f"{label}[{index}] lacks kind")
        kinds.append(kind)
        if kind == "text_start":
            _require(not text, f"{label}: nested text lifecycle")
            text = True
        elif kind == "text_delta":
            _require(
                text and bool(event.get("delta")),
                f"{label}: text delta outside lifecycle",
            )
        elif kind == "text_end":
            _require(text, f"{label}: text end without start")
            text = False
        elif kind == "thinking_start":
            _require(not thinking, f"{label}: nested thinking lifecycle")
            thinking = True
        elif kind == "thinking_delta":
            _require(
                thinking and bool(event.get("delta")),
                f"{label}: thinking delta outside lifecycle",
            )
        elif kind == "thinking_end":
            _require(thinking, f"{label}: thinking end without start")
            thinking = False
        elif kind == "tool_call_start":
            call_id = str(event.get("call_id") or "")
            _require(call_id and call_id not in tools, f"{label}: invalid tool start")
            tools.add(call_id)
        elif kind == "tool_call_delta":
            _require(
                str(event.get("call_id") or "") in tools,
                f"{label}: tool delta outside lifecycle",
            )
        elif kind == "tool_call_end":
            call_id = str(event.get("call_id") or "")
            _require(call_id in tools, f"{label}: tool end without start")
            tools.remove(call_id)
    _require(not text and not thinking and not tools, f"{label}: unbalanced lifecycle")
    return kinds


def _oracle_observations(row: Mapping[str, Any]) -> Mapping[str, Any]:
    output = _mapping(row.get("output"), "oracle output")
    value = output.get("observations", output)
    return _mapping(value, "oracle observations")


def _trace_projection(value: Any, label: str) -> Mapping[str, Any]:
    try:
        return validate_semantic_trace(_mapping(value, label))
    except ContractError as exc:
        _fail(f"{label} is not a valid semantic trace: {exc}")
    raise AssertionError("unreachable")


def _provider_projection(row_id: str, observed: Mapping[str, Any]) -> Any:
    _, family = row_id.split(".", 1)
    if family == "catalog_model_route":
        return _mapping(
            observed.get("canonical_projection"), "catalog semantic projection"
        )
    if family == "error_terminal":
        values = _sequence(observed.get("semantic_traces"), "error semantic traces")
        result = []
        for index, value in enumerate(values):
            item = _mapping(value, f"error semantic trace {index}")
            result.append(
                {
                    "label": item.get("label"),
                    "trace": _trace_projection(
                        item.get("trace"), f"error semantic trace {index}"
                    ),
                }
            )
        return result
    if family == "cancel_terminal":
        result = {}
        for phase in ("before_output", "after_partial"):
            item = _mapping(observed.get(phase), f"cancellation {phase}")
            result[phase] = _trace_projection(
                item.get("semantic_trace"), f"cancellation {phase}"
            )
        return result
    return _trace_projection(observed.get("semantic_trace"), f"{row_id} semantic trace")


def _check_oracle_provider(row_id: str, row: Mapping[str, Any]) -> Any:
    return _provider_projection(row_id, _oracle_observations(row))


def _check_catalog(provider: str, observed: Mapping[str, Any]) -> Mapping[str, Any]:
    owner, aliases, auth_schemes = _EXPECTED_AUTH[provider]
    runtime = _EXPECTED_RUNTIME[provider]
    _require(
        observed.get("provider_id") == provider, "candidate catalog provider mismatch"
    )
    _require(
        observed.get("runtime_id") == runtime
        and observed.get("catalog_runtime_id") == runtime,
        "candidate catalog/runtime route mismatch",
    )
    _require(observed.get("aliases") == aliases, "candidate provider aliases mismatch")
    _require(
        observed.get("auth_schemes") == auth_schemes
        and observed.get("auth_owner") == owner,
        "candidate provider auth contract mismatch",
    )
    _require(observed.get("support_tier") == "core", "candidate provider is not core")
    _require(
        isinstance(observed.get("model"), str)
        and observed.get("route") in {"direct", "routed"},
        "candidate model route mismatch",
    )
    _require(
        observed.get("supports_streaming") is True,
        "candidate provider is not streaming",
    )
    return _mapping(
        observed.get("canonical_projection"), "candidate catalog semantic projection"
    )


def _check_provider_scenario(
    row_id: str, observed: Mapping[str, Any], oracle_row: Mapping[str, Any]
) -> None:
    expected_input = oracle_row.get("input")
    expected_hash = oracle_row.get("input_sha256")
    actual_input = observed.get("scenario_input")
    actual_hash = observed.get("scenario_input_sha256")
    _require(
        canonical_json(actual_input) == canonical_json(expected_input),
        "provider scenario input mismatch",
    )
    _require(actual_hash == expected_hash, "provider scenario input hash mismatch")
    _require(
        _oracle_hash(actual_input) == expected_hash,
        "provider scenario hash is not exact",
    )


def _check_candidate_provider(
    row_id: str, observation: Mapping[str, Any], oracle_row: Mapping[str, Any]
) -> Any:
    observed = _mapping(observation.get("observed"), "candidate observed value")
    _check_provider_scenario(row_id, observed, oracle_row)
    _, family = row_id.split(".", 1)
    if family == "catalog_model_route":
        return _check_catalog(row_id.split(".", 1)[0], observed)
    projection = _provider_projection(row_id, observed)
    if family in {
        "request_ir",
        "text_stream",
        "tool_stream",
        "usage_finish",
    }:
        visible = _mapping(
            observed.get("model_visible_request"),
            "candidate model-visible request",
        )
        route = _mapping(visible.get("route"), "candidate visible route")
        expected_request = _mapping(oracle_row.get("input"), "oracle provider input")[
            "request"
        ]
        _require(
            visible.get("logical_request")
            == {
                "messages": expected_request["messages"],
                "tools": expected_request["tools"],
            },
            "candidate model-visible request changed the shared scenario",
        )
        provider = row_id.split(".", 1)[0]
        expected_model = str(
            _mapping(oracle_row.get("input"), "oracle provider input")["model"]["id"]
        ).split("/", 1)[1]
        _require(
            route
            == {
                "provider_id": provider,
                "runtime_id": _EXPECTED_RUNTIME[provider],
                "model": expected_model,
            },
            "candidate model-visible route changed",
        )
    if family in {"tool_stream", "usage_finish"}:
        replay = _mapping(observed.get("tool_result_replay"), "tool-result replay")
        _require(
            replay
            == {"call_id": "call-fixture", "content": '{"ok":true}', "is_error": False},
            "tool-result replay changed",
        )
    if family == "cancel_terminal":
        _require(
            observed.get("terminal_count") == 1,
            "cancellation terminal count changed",
        )
        _require(
            observed.get("unsafe_replay") is False,
            "cancellation permits unsafe replay",
        )
        for phase in ("before_output", "after_partial"):
            item = _mapping(observed.get(phase), f"candidate cancellation {phase}")
            _require(
                item.get("retry_attempts") == 0 and item.get("fallback_attempts") == 0,
                "cancellation retry count changed",
            )
            _require(
                item.get("runtime_invocations") == 1
                and item.get("route_failure_count") == 0
                and item.get("terminal_count") == 1
                and item.get("cancel_signal_observed") is True,
                "cancellation did not exercise one runtime terminal",
            )
    if family == "error_terminal":
        _require(
            observed.get("secrets_or_transport_headers") is False
            and observed.get("replay_after_output") is False,
            "error evidence leaks or replays",
        )
        failures = _sequence(
            observed.get("raw_provider_failures"),
            "candidate provider failure probes",
        )
        _require(
            len(failures) == 3,
            "candidate provider failure taxonomy is incomplete",
        )
        for index, expected_label in enumerate(("auth", "rate_limit", "malformed")):
            item = _mapping(failures[index], f"candidate provider failure {index}")
            failure = _mapping(
                item.get("runtime_failure"),
                f"candidate provider failure {index} runtime result",
            )
            runtime_events = _sequence(
                failure.get("runtime_events"),
                f"candidate provider failure {index} runtime events",
            )
            _require(
                item.get("label") == expected_label
                and failure.get("output_emitted") is False
                and failure.get("replay_safe") is True
                and all(
                    _mapping(event, "candidate failure event").get("kind")
                    == "response_start"
                    for event in runtime_events
                )
                and len(runtime_events) <= 1,
                "candidate provider failure was not replay-safe",
            )
            _require(
                failure.get("kind")
                == ("protocol" if expected_label == "malformed" else "provider"),
                "candidate provider failure classification changed",
            )
    return projection


def _check_artifact(row_id: str, observed: Mapping[str, Any]) -> None:
    provider_ids = ["codex", "openai", "anthropic", "openrouter"]
    if row_id == ARTIFACT_ROW_IDS[0]:
        _require(
            observed.get("package_origin") == "installed_wheel",
            "artifact did not originate from installed wheel",
        )
        _require(
            observed.get("provider_ids") == provider_ids
            and observed.get("credentials_empty") is True,
            "installed wheel catalog/auth mismatch",
        )
        _require(
            observed.get("role_schema") == "bb.effective_model_role_lock.v1"
            and observed.get("role_route") == "mock/reference",
            "installed wheel role contract mismatch",
        )
        _require(
            isinstance(observed.get("role_lock_hash"), str)
            and str(observed.get("role_lock_hash")).startswith("sha256:")
            and len(str(observed.get("role_lock_hash"))) == 71,
            "installed wheel role hash missing",
        )
    elif row_id == ARTIFACT_ROW_IDS[1]:
        _require(
            observed.get("provider_ids") == provider_ids
            and observed.get("credentials_empty") is True,
            "packed SDK catalog/auth mismatch",
        )
        _require(
            observed.get("role_schema") == "bb.effective_model_role_lock.v1"
            and observed.get("role_route") == "mock/reference",
            "packed SDK role contract mismatch",
        )
        _require(
            isinstance(observed.get("role_lock_hash"), str)
            and str(observed.get("role_lock_hash")).startswith("sha256:")
            and len(str(observed.get("role_lock_hash"))) == 71,
            "packed SDK role hash missing",
        )
    else:
        _require(
            observed.get("transport") == "loopback_http_sse"
            and observed.get("terminal_event_observed") is True,
            "installed end-to-end transport did not terminate",
        )
        _require(
            observed.get("exchange_ref_matches") is True,
            "installed exchange reference mismatch",
        )
        _require(
            observed.get("provider_id") == "smoke"
            and observed.get("runtime_id") == "smoke_chat",
            "installed trace provider identity mismatch",
        )
        _require(
            observed.get("request_roles") == ["system", "user"],
            "installed trace request roles changed",
        )
        kinds = _sequence(observed.get("event_kinds"), "installed trace event kinds")
        expected_kinds = [
            "response_start",
            "text_start",
            "text_delta",
            "text_end",
        ]
        _require(
            list(kinds) == expected_kinds,
            "installed trace stream lifecycle is incomplete",
        )
        events = [
            _mapping(value, f"installed trace event {index}")
            for index, value in enumerate(
                _sequence(observed.get("events"), "installed trace events")
            )
        ]
        _require(
            [event.get("sequence") for event in events] == [0, 1, 2, 3],
            "installed trace event sequence changed",
        )
        _require(
            all(
                events[index].get("content_index") == 0
                and events[index].get("message_id") == "smoke-message-1"
                for index in (1, 2, 3)
            ),
            "installed trace text identity changed",
        )
        expected_text = "Hi! All systems nominal."
        _require(
            events[2].get("delta") == expected_text
            and observed.get("stream_text") == expected_text
            and observed.get("assistant_text") == expected_text,
            "installed trace streamed and terminal text disagree",
        )
        _require(
            observed.get("terminal_kind") == "done"
            and observed.get("finish_reason") == "stop"
            and observed.get("output_emitted") is True,
            "installed trace terminal mismatch",
        )


def _check_oracle_auth_role(
    row_id: str, oracle_row: Mapping[str, Any]
) -> Mapping[str, Any]:
    observations = _oracle_observations(oracle_row)
    projection = _mapping(
        observations.get("semantic_projection"),
        f"F1 auth/role semantic projection {row_id}",
    )
    execution = _mapping(
        observations.get("execution"), f"F1 auth/role execution {row_id}"
    )
    _require(
        execution
        == {
            "actual_api_calls": True,
            "network_denied": True,
            "secrets_redacted": True,
        },
        "F1 auth/role row lacks actual hermetic execution",
    )
    return projection


def _candidate_auth_role_projection(
    row_id: str, observation: Mapping[str, Any]
) -> Mapping[str, Any]:
    observed = _mapping(observation.get("observed"), "candidate auth/role observation")
    api_observation = _mapping(
        observed.get("api_observation"), "candidate auth/role API observation"
    )
    projection = _mapping(
        observed.get("semantic_projection"),
        "candidate auth/role semantic projection",
    )
    execution = _mapping(observed.get("execution"), "candidate auth/role execution")
    api_calls = _sequence(execution.get("api_calls"), "candidate auth/role API calls")
    _require(
        set(execution)
        == {
            "actual_api_calls",
            "api_calls",
            "network_denied",
            "secrets_redacted",
        }
        and execution.get("actual_api_calls") is True
        and execution.get("network_denied") is True
        and execution.get("secrets_redacted") is True
        and bool(api_calls),
        "candidate auth/role row lacks actual hermetic execution",
    )
    api_call_set = {str(call) for call in api_calls}
    required_calls = AUTH_ROLE_REQUIRED_API_CALLS.get(row_id)
    _require(
        all(
            isinstance(call, str) and call in AUTH_ROLE_PUBLIC_API_CALLS
            for call in api_calls
        )
        and list(api_calls) == sorted(api_call_set)
        and required_calls is not None
        and required_calls.issubset(api_call_set),
        "candidate auth/role row lacks approved public API evidence",
    )
    _require(
        projection == project_auth_role_observation(row_id, api_observation),
        "candidate auth/role semantic projection is not bound to API observation",
    )
    evidence = _mapping(observation.get("evidence"), "candidate auth/role evidence")
    _require(
        "canary"
        not in canonical_json({"observed": observed, "evidence": evidence}).lower(),
        "candidate auth/role secret canary escaped",
    )
    _require(
        evidence.get("api_observation_sha256")
        == auth_role_observation_sha256(api_observation),
        "candidate auth/role API observation hash mismatch",
    )
    return projection


def _check_candidate(
    row_id: str,
    observation: Mapping[str, Any],
    oracle_row: Mapping[str, Any] | None = None,
) -> Any:
    if row_id in AUTH_ROLE_ROW_IDS:
        return _candidate_auth_role_projection(row_id, observation)
    observed = _mapping(observation.get("observed"), "candidate observed value")
    if row_id in ARTIFACT_ROW_IDS:
        _check_artifact(row_id, observed)
        return None
    if oracle_row is None:
        _fail("provider row lacks oracle input")
    return _check_candidate_provider(row_id, observation, oracle_row)


def evaluate(
    matrix: Mapping[str, Any],
    oracle: Mapping[str, Any],
    observations: Sequence[Mapping[str, Any]],
    *,
    reference_tests_passed: bool,
    include_artifacts: bool,
) -> Evaluation:
    expected = ALL_ROW_IDS if include_artifacts else _SOURCE_ROW_IDS
    if include_artifacts is False and any(
        str(observation.get("row_id", "")).startswith("artifact.")
        for observation in observations
    ):
        raise DifferentialError("source-only evaluation received artifact rows")
    validate_row_set(
        [observation.get("row_id") for observation in observations], expected=expected
    )
    observation_by_id: dict[str, Mapping[str, Any]] = {}
    for observation in observations:
        validated = validate_observation(observation)
        observation_by_id[str(validated["row_id"])] = validated
    matrix_by_id = {str(row["row_id"]): row for row in matrix["rows"]}
    oracle_by_id = {str(row["id"]): row for row in oracle["rows"]}
    comparisons: list[Comparison] = []
    for row_id in expected:
        matrix_row = matrix_by_id[row_id]
        oracle_row = oracle_by_id.get(row_id)
        if row_id in AUTH_ROLE_ROW_IDS:
            if oracle_row is None:
                raise DifferentialError(f"missing oracle row {row_id}")
            flags = _oracle_observations(oracle_row)
            if (
                flags.get("reference_test_execution_required") is True
                and not reference_tests_passed
            ):
                comparisons.append(
                    Comparison(
                        row_id, "unverified", "exact F1 reference tests did not pass"
                    )
                )
                continue
            if flags.get("source_derived") is not True:
                comparisons.append(
                    Comparison(
                        row_id, "unverified", "F1 auth/role row is not source-derived"
                    )
                )
                continue
            expected_divergence = row_id in INTENTIONAL_DIVERGENCES
            if (
                flags.get("divergence_expected") is not expected_divergence
                or flags.get("portable_equivalent") is expected_divergence
            ):
                comparisons.append(
                    Comparison(
                        row_id,
                        "unverified",
                        "F1 divergence flags disagree with the matrix",
                    )
                )
                continue
        oracle_projection: Any = None
        if row_id not in ARTIFACT_ROW_IDS:
            if oracle_row is None:
                raise DifferentialError(f"missing oracle row {row_id}")
            try:
                oracle_projection = (
                    _check_oracle_auth_role(row_id, oracle_row)
                    if row_id in AUTH_ROLE_ROW_IDS
                    else _check_oracle_provider(row_id, oracle_row)
                )
            except _PredicateFailure as exc:
                comparisons.append(Comparison(row_id, "unverified", str(exc)))
                continue
        try:
            candidate_projection = _check_candidate(
                row_id, observation_by_id[row_id], oracle_row
            )
        except _PredicateFailure as exc:
            comparisons.append(Comparison(row_id, "BreadBoard_defect", str(exc)))
            continue
        if row_id not in ARTIFACT_ROW_IDS:
            same_projection = canonical_json(candidate_projection) == canonical_json(
                oracle_projection
            )
            if row_id in INTENTIONAL_DIVERGENCES and same_projection:
                comparisons.append(
                    Comparison(
                        row_id,
                        "unverified",
                        "declared semantic divergence was not observed",
                    )
                )
                continue
            if row_id not in INTENTIONAL_DIVERGENCES and not same_projection:
                comparisons.append(
                    Comparison(
                        row_id, "BreadBoard_defect", "semantic projection mismatch"
                    )
                )
                continue
        if row_id in INTENTIONAL_DIVERGENCES:
            divergence = matrix_row.get("allowed_divergence_ref")
            if not isinstance(divergence, str) or not divergence:
                comparisons.append(
                    Comparison(
                        row_id,
                        "BreadBoard_defect",
                        "intentional divergence lacks an approved reference",
                    )
                )
                continue
            comparisons.append(Comparison(row_id, "intentional_divergence", divergence))
        else:
            comparisons.append(
                Comparison(row_id, "match", str(matrix_row["comparator"]))
            )
    return Evaluation(tuple(comparisons))


__all__ = [
    "Comparison",
    "DifferentialError",
    "Evaluation",
    "INTENTIONAL_DIVERGENCES",
    "MATRIX_ID",
    "ORACLE_FIXTURE_SHA256",
    "evaluate",
    "load_matrix",
    "load_oracle",
]
