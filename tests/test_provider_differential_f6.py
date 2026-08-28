from __future__ import annotations

import copy
import runpy
import hashlib
import json
from pathlib import Path
from typing import Mapping

import pytest
import setuptools

from breadboard_engine.provider.contracts import (
    ProviderCorrelation,
    ProviderExchangeRecorder,
    ProviderIdentity,
    ProviderRequest,
    ProviderRuntimeContext,
)
from breadboard_engine.provider.routing import (
    AnthropicToolTranslator,
    OpenAIToolTranslator,
    provider_router,
)
from breadboard_engine.provider.runtimes.testing import SmokeRuntime
from conformance.provider_differential import artifact_rows
from conformance.provider_differential.auth_role_rows import (
    AUTH_ROLE_ROW_IDS,
    auth_role_observation_sha256,
    observe_auth_role_row,
    project_auth_role_observation,
)
from conformance.provider_differential.contracts import (
    ALL_ROW_IDS,
    ARTIFACT_ROW_IDS,
    ORACLE_IDENTITY,
    PROVIDER_ROW_IDS,
    ContractError,
    canonical_json,
    sha256_file,
    validate_manifest,
    validate_row_set,
)
from conformance.provider_differential.gate import (
    INTENTIONAL_DIVERGENCES,
    DifferentialError,
    evaluate,
    load_matrix,
    load_oracle,
)
from conformance.provider_differential.provider_rows import observe_provider_row
from scripts.quality import run_provider_differential

ROOT = Path(__file__).parents[1]
MATRIX_PATH = ROOT / "conformance/provider_differential/matrix.v1.json"
ORACLE_PATH = ROOT / "conformance/provider_differential/oracle/omp-v18.0.1.json"
RUNNER_PATH = ROOT / "scripts/quality/capture_f6_omp_oracle.ts"


@pytest.fixture(scope="module")
def matrix():
    return load_matrix(MATRIX_PATH)


@pytest.fixture(scope="module")
def oracle():
    return load_oracle(ORACLE_PATH, RUNNER_PATH)


@pytest.fixture(scope="module")
def source_observations(tmp_path_factory, oracle):
    root = tmp_path_factory.mktemp("f6-auth-role").resolve()
    oracle_by_id = {row["id"]: row for row in oracle["rows"]}
    rows = [
        observe_provider_row(row_id, oracle_by_id[row_id]["input"])
        for row_id in PROVIDER_ROW_IDS
    ]
    rows.extend(
        observe_auth_role_row(row_id, root=root / row_id.replace(".", "-"))
        for row_id in AUTH_ROLE_ROW_IDS
    )
    return rows


def _artifact_observations() -> list[dict]:
    shared = {
        "provider_ids": ["codex", "openai", "anthropic", "openrouter"],
        "auth_schemes": {
            "codex": ["provider_managed"],
            "openai": ["api_key"],
            "anthropic": ["api_key", "oauth2"],
            "openrouter": ["api_key"],
        },
        "credentials_empty": True,
        "role_schema": "bb.effective_model_role_lock.v1",
        "role_route": "mock/reference",
        "role_lock_hash": "sha256:" + "a" * 64,
    }
    return [
        {
            "row_id": ARTIFACT_ROW_IDS[0],
            "subject": "installed Python engine",
            "claim": "installed wheel contract",
            "observed": {"package_origin": "installed_wheel", **shared},
            "evidence": ["wheel:sha256:" + "b" * 64],
        },
        {
            "row_id": ARTIFACT_ROW_IDS[1],
            "subject": "packed TypeScript SDK",
            "claim": "packed SDK contract",
            "observed": dict(shared),
            "evidence": ["packed-sdk:sha256:" + "c" * 64],
        },
        {
            "row_id": ARTIFACT_ROW_IDS[2],
            "subject": "installed wheel plus packed SDK",
            "claim": "installed trace contract",
            "observed": {
                "transport": "loopback_http_sse",
                "terminal_event_observed": True,
                "exchange_ref_matches": True,
                "provider_id": "smoke",
                "runtime_id": "smoke_chat",
                "request_roles": ["system", "user"],
                "events": [
                    {"sequence": 0, "kind": "response_start"},
                    {
                        "sequence": 1,
                        "kind": "text_start",
                        "content_index": 0,
                        "message_id": "smoke-message-1",
                    },
                    {
                        "sequence": 2,
                        "kind": "text_delta",
                        "content_index": 0,
                        "message_id": "smoke-message-1",
                        "delta": "Hi! All systems nominal.",
                    },
                    {
                        "sequence": 3,
                        "kind": "text_end",
                        "content_index": 0,
                        "message_id": "smoke-message-1",
                    },
                ],
                "event_kinds": [
                    "response_start",
                    "text_start",
                    "text_delta",
                    "text_end",
                ],
                "stream_text": "Hi! All systems nominal.",
                "assistant_text": "Hi! All systems nominal.",
                "terminal_kind": "done",
                "finish_reason": "stop",
                "output_emitted": True,
                "usage": None,
            },
            "evidence": ["loopback:http+sse"],
        },
    ]


def test_oracle_subprocess_environment_is_allowlisted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PATH", "/safe/bin")
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret")
    monkeypatch.setenv("GH_TOKEN", "gh-secret")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "aws-secret")
    monkeypatch.setenv("UNRELATED_AMBIENT_VALUE", "ambient")

    environment = run_provider_differential._safe_environment()

    assert environment["PATH"] == "/safe/bin"
    assert {
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "AWS_SECRET_ACCESS_KEY",
        "UNRELATED_AMBIENT_VALUE",
    }.isdisjoint(environment)
    assert set(environment) <= (
        run_provider_differential._SAFE_ORACLE_ENV
        | {
            "HTTP_PROXY",
            "HTTPS_PROXY",
            "ALL_PROXY",
            "NO_PROXY",
            "BUN_TELEMETRY_DISABLE",
        }
    )


def test_artifact_environment_strips_credential_control_locators(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    control_environment = {
        "BREADBOARD_AUTH_BROKER_CONFIGURED": "1",
        "BREADBOARD_AUTH_BROKER_TOKEN": "broker-token",
        "BREADBOARD_AUTH_BROKER_URL": "https://broker.invalid/session",
        "BREADBOARD_CREDENTIAL_DB": str(tmp_path / "credentials.sqlite3"),
        "BREADBOARD_CREDENTIAL_STORE_PATH": str(tmp_path / "store.sqlite3"),
        "BREADBOARD_STATE_DIR": str(tmp_path / "state"),
    }
    for name, value in control_environment.items():
        monkeypatch.setenv(name, value)

    environment = artifact_rows._safe_environment(home=tmp_path / "home")

    assert control_environment.keys().isdisjoint(environment)


def test_wheel_provenance_rejects_checkout_identity_overrides(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setuptools, "setup", lambda **_kwargs: None)
    namespace = runpy.run_path(str(ROOT / "setup.py"), run_name="bb_setup_test")
    actual_repository = "https://github.com/kmccleary3301/breadboard.git"
    actual_commit = "a" * 40
    actual_tree = "b" * 40

    def fake_git(*arguments: str) -> str:
        if arguments[:4] == (
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
        ):
            assert "breadboard" in arguments
            assert "contracts" in arguments
            assert "agent_configs" in arguments
            return ""
        values = {
            ("rev-parse", "--is-inside-work-tree"): "true",
            ("remote", "get-url", "origin"): actual_repository,
            ("rev-parse", "HEAD"): actual_commit,
            ("rev-parse", "HEAD^{tree}"): actual_tree,
        }
        return values[arguments]

    namespace["_source_identity"].__globals__["_git"] = fake_git
    monkeypatch.setenv("BREADBOARD_BUILD_SOURCE_REPOSITORY", actual_repository)
    monkeypatch.setenv("BREADBOARD_BUILD_SOURCE_COMMIT", "c" * 40)
    monkeypatch.setenv("BREADBOARD_BUILD_SOURCE_TREE", actual_tree)

    with pytest.raises(RuntimeError, match="do not match the Git checkout"):
        namespace["_source_identity"]()

def test_wheel_provenance_rejects_dirty_non_engine_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(setuptools, "setup", lambda **_kwargs: None)
    namespace = runpy.run_path(str(ROOT / "setup.py"), run_name="bb_setup_test")

    def fake_git(*arguments: str) -> str:
        if arguments == ("rev-parse", "--is-inside-work-tree"):
            return "true"
        if arguments[:4] == (
            "status",
            "--porcelain",
            "--untracked-files=all",
            "--",
        ):
            return "?? breadboard/untracked_runtime.py"
        raise AssertionError(arguments)

    namespace["_source_identity"].__globals__["_git"] = fake_git

    with pytest.raises(RuntimeError, match="clean wheel build inputs"):
        namespace["_source_identity"]()


def test_matrix_and_oracle_are_exact_and_bound_to_f1(matrix, oracle):
    assert matrix["oracle_identity"] == ORACLE_IDENTITY
    assert matrix["required_row_count"] == 45
    assert len(matrix["rows"]) == 45
    assert oracle["oracle"]["head"] == ORACLE_IDENTITY["commit"]
    assert oracle["oracle"]["tree"] == ORACLE_IDENTITY["tree"]
    assert len(oracle["rows"]) == 42


def test_all_source_rows_are_claimable(matrix, oracle, source_observations):
    result = evaluate(
        matrix,
        oracle,
        source_observations,
        reference_tests_passed=True,
        include_artifacts=False,
    )

    assert result.claimable is True
    assert result.counts == {
        "match": 39,
        "intentional_divergence": 3,
        "BreadBoard_defect": 0,
        "unverified": 0,
    }
    assert {
        row.row_id
        for row in result.comparisons
        if row.classification == "intentional_divergence"
    } == INTENTIONAL_DIVERGENCES


def test_all_45_rows_are_claimable_with_artifact_contracts(
    matrix, oracle, source_observations
):
    result = evaluate(
        matrix,
        oracle,
        [*source_observations, *_artifact_observations()],
        reference_tests_passed=True,
        include_artifacts=True,
    )

    assert result.claimable is True
    assert result.counts["match"] == 42
    assert len(result.comparisons) == 45


@pytest.mark.parametrize(
    ("row_id", "mutate", "detail"),
    [
        (
            "openai.request_ir",
            lambda observed: observed["semantic_trace"]["request"]["tools"][0].update(
                {"description": "Changed schema description"}
            ),
            "semantic projection mismatch",
        ),
        (
            "anthropic.text_stream",
            lambda observed: observed["semantic_trace"]["result"].update(
                {"assembled_text": "hellohello"}
            ),
            "semantic text events and result disagree",
        ),
        (
            "codex.usage_finish",
            lambda observed: observed["semantic_trace"]["usage"].update(
                {"input_tokens": None}
            ),
            "semantic projection mismatch",
        ),
        (
            "openrouter.cancel_terminal",
            lambda observed: observed["after_partial"].update({"retry_attempts": 1}),
            "cancellation retry count changed",
        ),
        (
            "auth.refresh_single_flight",
            lambda observed: observed["api_observation"].update({"provider_calls": 2}),
            "not bound to API observation",
        ),
    ],
)
def test_plausible_semantic_regressions_classify_as_defects(
    matrix,
    oracle,
    source_observations,
    row_id,
    mutate,
    detail,
):
    rows = copy.deepcopy(source_observations)
    target = next(row for row in rows if row["row_id"] == row_id)
    mutate(target["observed"])

    result = evaluate(
        matrix,
        oracle,
        rows,
        reference_tests_passed=True,
        include_artifacts=False,
    )
    comparison = next(row for row in result.comparisons if row.row_id == row_id)

    assert comparison.classification == "BreadBoard_defect"
    assert detail in comparison.detail
    assert result.claimable is False


def test_auth_role_rows_compare_candidate_to_actual_oracle_semantics(
    matrix, oracle, source_observations
):
    changed_oracle = copy.deepcopy(oracle)
    target = next(
        row
        for row in changed_oracle["rows"]
        if row["id"] == "auth.refresh_single_flight"
    )
    target["output"]["observations"]["semantic_projection"]["workers_converged"] = False

    result = evaluate(
        matrix,
        changed_oracle,
        source_observations,
        reference_tests_passed=True,
        include_artifacts=False,
    )
    comparison = next(row for row in result.comparisons if row.row_id == target["id"])

    assert comparison.classification == "BreadBoard_defect"
    assert comparison.detail == "semantic projection mismatch"


def test_candidate_auth_api_result_is_compared_to_oracle_not_local_expectation(
    matrix, oracle, source_observations
):
    rows = copy.deepcopy(source_observations)
    target = next(row for row in rows if row["row_id"] == "auth.refresh_single_flight")
    api_observation = target["observed"]["api_observation"]
    api_observation["provider_calls"] = 2
    target["observed"]["semantic_projection"] = project_auth_role_observation(
        target["row_id"], api_observation
    )
    target["evidence"]["api_observation_sha256"] = auth_role_observation_sha256(
        api_observation
    )

    result = evaluate(
        matrix,
        oracle,
        rows,
        reference_tests_passed=True,
        include_artifacts=False,
    )
    comparison = next(
        row for row in result.comparisons if row.row_id == target["row_id"]
    )

    assert comparison.classification == "BreadBoard_defect"
    assert comparison.detail == "semantic projection mismatch"


def test_candidate_auth_role_requires_actual_hermetic_api_evidence(
    matrix, oracle, source_observations
):
    rows = copy.deepcopy(source_observations)
    target = next(row for row in rows if row["row_id"] == "role.public_alias_selection")
    target["observed"]["execution"]["actual_api_calls"] = False

    result = evaluate(
        matrix,
        oracle,
        rows,
        reference_tests_passed=True,
        include_artifacts=False,
    )
    comparison = next(
        row for row in result.comparisons if row.row_id == target["row_id"]
    )

    assert comparison.classification == "BreadBoard_defect"
    assert (
        comparison.detail == "candidate auth/role row lacks actual hermetic execution"
    )


def test_candidate_auth_role_rejects_unapproved_api_provenance(
    matrix, oracle, source_observations
):
    rows = copy.deepcopy(source_observations)
    target = next(row for row in rows if row["row_id"] == "role.public_alias_selection")
    target["observed"]["execution"]["api_calls"] = [
        "breadboard_engine.provider_broker.evil"
    ]

    result = evaluate(
        matrix,
        oracle,
        rows,
        reference_tests_passed=True,
        include_artifacts=False,
    )
    comparison = next(
        row for row in result.comparisons if row.row_id == target["row_id"]
    )

    assert comparison.classification == "BreadBoard_defect"
    assert (
        comparison.detail
        == "candidate auth/role row lacks approved public API evidence"
    )


def test_candidate_auth_role_gate_rejects_rebound_secret_canary(
    matrix, oracle, source_observations
):
    rows = copy.deepcopy(source_observations)
    target = next(row for row in rows if row["row_id"] == "role.public_alias_selection")
    api_observation = target["observed"]["api_observation"]
    api_observation["diagnostic"] = "auth-role-leak-canary"
    target["evidence"]["api_observation_sha256"] = auth_role_observation_sha256(
        api_observation
    )

    result = evaluate(
        matrix,
        oracle,
        rows,
        reference_tests_passed=True,
        include_artifacts=False,
    )
    comparison = next(
        row for row in result.comparisons if row.row_id == target["row_id"]
    )

    assert comparison.classification == "BreadBoard_defect"
    assert comparison.detail == "candidate auth/role secret canary escaped"


def test_auth_role_oracle_requires_actual_hermetic_execution(
    matrix, oracle, source_observations
):
    changed_oracle = copy.deepcopy(oracle)
    target = next(
        row
        for row in changed_oracle["rows"]
        if row["id"] == "role.public_alias_selection"
    )
    target["output"]["observations"]["execution"]["actual_api_calls"] = False

    result = evaluate(
        matrix,
        changed_oracle,
        source_observations,
        reference_tests_passed=True,
        include_artifacts=False,
    )
    comparison = next(row for row in result.comparisons if row.row_id == target["id"])

    assert comparison.classification == "unverified"
    assert comparison.detail == "F1 auth/role row lacks actual hermetic execution"


def test_cancellation_rows_execute_each_runtime_without_retry(source_observations):
    for provider in ("codex", "openai", "anthropic", "openrouter"):
        row = next(
            item
            for item in source_observations
            if item["row_id"] == f"{provider}.cancel_terminal"
        )
        before = row["observed"]["before_output"]
        after = row["observed"]["after_partial"]
        for phase in (before, after):
            assert phase["runtime_invocations"] == 1
            assert phase["retry_attempts"] == 0
            assert phase["fallback_attempts"] == 0
            assert phase["route_failure_count"] == 0
            assert phase["terminal_count"] == 1
            assert phase["cancel_signal_observed"] is True
        assert [event["kind"] for event in before["raw_runtime_events"]] == [
            "response_start"
        ]
        assert [event["kind"] for event in after["raw_runtime_events"]] == [
            "response_start",
            "text_start",
            "text_delta",
        ]
        assert before["semantic_trace"]["terminal"]["output_emitted"] is False
        assert after["semantic_trace"]["terminal"]["output_emitted"] is True


def test_reference_test_failure_is_unverified(matrix, oracle, source_observations):
    result = evaluate(
        matrix,
        oracle,
        source_observations,
        reference_tests_passed=False,
        include_artifacts=False,
    )

    assert result.counts["unverified"] == len(AUTH_ROLE_ROW_IDS)
    assert result.claimable is False


def test_row_set_rejects_missing_and_duplicate_rows():
    with pytest.raises(ContractError, match="expected 45 rows"):
        validate_row_set(ALL_ROW_IDS[:-1])
    with pytest.raises(ContractError, match="duplicate row ID"):
        validate_row_set((*ALL_ROW_IDS[:-1], ALL_ROW_IDS[0]))


def test_oracle_loader_rejects_runner_drift(tmp_path):
    changed_runner = tmp_path / "runner.ts"
    changed_runner.write_text("console.log('different')\n", encoding="utf-8")

    with pytest.raises(DifferentialError, match="runner hash mismatch"):
        load_oracle(ORACLE_PATH, changed_runner)


def test_oracle_loader_rejects_tamper_with_recomputed_row_hash(tmp_path):
    fixture = json.loads(ORACLE_PATH.read_text(encoding="utf-8"))
    row = next(item for item in fixture["rows"] if item["id"] == "openai.text_stream")
    row["output"]["observations"]["semantic_trace"]["result"]["assembled_text"] = (
        "tampered"
    )
    row["output_sha256"] = (
        "sha256:"
        + hashlib.sha256(
            (canonical_json(row["output"]) + "\n").encode("utf-8")
        ).hexdigest()
    )
    tampered = tmp_path / "oracle.json"
    tampered.write_text(canonical_json(fixture) + "\n", encoding="utf-8")

    with pytest.raises(DifferentialError, match="pinned digest mismatch"):
        load_oracle(tampered, RUNNER_PATH)


def test_provider_observer_rejects_a_different_scenario_input(oracle):
    oracle_row = next(
        row for row in oracle["rows"] if row["id"] == "openai.text_stream"
    )
    changed = copy.deepcopy(oracle_row["input"])
    changed["fake"]["delta"] = "different"

    with pytest.raises(ValueError, match="scenario input mismatch"):
        observe_provider_row("openai.text_stream", changed)


def test_candidate_scenario_drift_is_a_defect(matrix, oracle, source_observations):
    rows = copy.deepcopy(source_observations)
    target = next(row for row in rows if row["row_id"] == "openai.text_stream")
    target["observed"]["scenario_input"]["fake"]["delta"] = "different"
    target["observed"]["scenario_input_sha256"] = (
        "sha256:"
        + hashlib.sha256(
            (canonical_json(target["observed"]["scenario_input"]) + "\n").encode(
                "utf-8"
            )
        ).hexdigest()
    )

    result = evaluate(
        matrix,
        oracle,
        rows,
        reference_tests_passed=True,
        include_artifacts=False,
    )
    comparison = next(
        row for row in result.comparisons if row.row_id == "openai.text_stream"
    )

    assert comparison.classification == "BreadBoard_defect"
    assert comparison.detail == "provider scenario input mismatch"


def test_smoke_runtime_emits_complete_stream_lifecycle():
    recorder = ProviderExchangeRecorder(
        correlation=ProviderCorrelation(
            session_id="session",
            input_id="input",
            turn_id="turn",
        ),
        provider=ProviderIdentity(
            provider_id="smoke",
            runtime_id="smoke_chat",
            route_id="smoke/reference",
            model="reference",
        ),
        request=ProviderRequest(
            stream=True,
            messages=[{"role": "user", "content": "smoke"}],
            tools=[],
        ),
    )
    context = ProviderRuntimeContext(
        session_state=None,
        agent_config={},
        stream=True,
        exchange_recorder=recorder,
    )

    descriptor, _model = provider_router.get_runtime_descriptor("smoke/reference")
    result = SmokeRuntime(descriptor).invoke(
        client={},
        model="reference",
        messages=[{"role": "user", "content": "smoke"}],
        tools=[],
        stream=True,
        context=context,
    )

    assert [event.kind for event in recorder.events] == [
        "response_start",
        "text_start",
        "text_delta",
        "text_end",
    ]
    assert recorder.events[2].delta == result.messages[0].content


@pytest.mark.parametrize(
    ("mutate", "detail"),
    [
        (
            lambda observed: observed.update(
                {
                    "events": observed["events"][:1],
                    "event_kinds": ["response_start"],
                }
            ),
            "stream lifecycle is incomplete",
        ),
        (
            lambda observed: observed["events"][2].update({"delta": "different"}),
            "streamed and terminal text disagree",
        ),
    ],
)
def test_installed_trace_requires_real_stream_output(
    matrix, oracle, source_observations, mutate, detail
):
    artifacts = _artifact_observations()
    mutate(artifacts[2]["observed"])

    result = evaluate(
        matrix,
        oracle,
        [*source_observations, *artifacts],
        reference_tests_passed=True,
        include_artifacts=True,
    )
    comparison = next(
        row for row in result.comparisons if row.row_id == ARTIFACT_ROW_IDS[2]
    )

    assert comparison.classification == "BreadBoard_defect"
    assert detail in comparison.detail


def test_tool_translators_preserve_full_json_schema():
    parameters = {
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
        "additionalProperties": False,
    }
    tool = {
        "name": "lookup",
        "description": "Lookup a file",
        "parameters": parameters,
    }

    openai = OpenAIToolTranslator().translate_tool_schema(tool)
    anthropic = AnthropicToolTranslator().translate_tool_schema(tool)

    assert openai["function"]["parameters"] == parameters
    assert anthropic["input_schema"] == parameters


def _valid_manifest(tmp_path: Path, matrix: Mapping[str, object]) -> dict:
    evidence = tmp_path / "evidence.json"
    evidence.write_text("{}\n", encoding="utf-8")
    record = {"path": evidence.name, "sha256": sha256_file(evidence)}
    commit = "1" * 40
    tree = "2" * 40
    matrix_rows = {row["row_id"]: row for row in matrix["rows"]}
    rows = []
    for row_id in ALL_ROW_IDS:
        classification = (
            "intentional_divergence" if row_id in INTENTIONAL_DIVERGENCES else "match"
        )
        row = {
            "row_id": row_id,
            "subject": matrix_rows[row_id]["subject"],
            "claim": matrix_rows[row_id]["claim"],
            "provider": row_id.split(".", 1)[0],
            "seam": matrix_rows[row_id]["seam"],
            "comparator": matrix_rows[row_id]["comparator"],
            "oracle_identity": dict(ORACLE_IDENTITY),
            "oracle_source_blobs": [record],
            "oracle_runner": "f6-runner",
            "oracle_input_sha256": "sha256:" + "3" * 64,
            "oracle_output_sha256": "sha256:" + "4" * 64,
            "breadboard_commit": commit,
            "breadboard_tree": tree,
            "evidence": [record],
            "classification": classification,
            "verification_toolchain": "pytest",
            "verified_at": "2026-08-25T00:00:00Z",
        }
        if classification == "intentional_divergence":
            row["divergence_ref"] = matrix_rows[row_id]["allowed_divergence_ref"]
        if row_id in ARTIFACT_ROW_IDS:
            row["artifact_provenance"] = {
                "artifact_id": row_id,
                "source": evidence.name,
                "sha256": record["sha256"],
            }
        rows.append(row)
    return {
        "schema_version": "bb.provider_differential_manifest.v1",
        "manifest_id": "f6-test",
        "matrix_id": matrix["matrix_id"],
        "created_at": "2026-08-25T00:00:00Z",
        "oracle_identity": dict(ORACLE_IDENTITY),
        "breadboard_commit": commit,
        "breadboard_tree": tree,
        "row_count": 45,
        "rows": rows,
    }


def test_manifest_validation_is_fail_closed(tmp_path, matrix):
    manifest = _valid_manifest(tmp_path, matrix)
    validate_manifest(manifest, root=tmp_path)

    nonclaimable = copy.deepcopy(manifest)
    nonclaimable["rows"][0]["classification"] = "BreadBoard_defect"
    with pytest.raises(ContractError, match="not claimable"):
        validate_manifest(nonclaimable, root=tmp_path)

    wrong_head = copy.deepcopy(manifest)
    wrong_head["rows"][0]["breadboard_commit"] = "5" * 40
    with pytest.raises(ContractError, match="differs from manifest"):
        validate_manifest(wrong_head, root=tmp_path)

    wrong_artifact = copy.deepcopy(manifest)
    artifact = next(
        row for row in wrong_artifact["rows"] if row["row_id"] in ARTIFACT_ROW_IDS
    )
    artifact["artifact_provenance"]["artifact_id"] = ARTIFACT_ROW_IDS[1]
    with pytest.raises(ContractError, match="id must match row"):
        validate_manifest(wrong_artifact, root=tmp_path)
