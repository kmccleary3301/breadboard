"""Pi lane terminal evidence preserves caught exceptions, not inferred verdicts."""
from breadboard_engine.compaction_lanes.pi_episode import _terminal_error_evidence
from breadboard_engine.provider.contracts import NativeProviderRequestFailure, ProviderRuntimeError
from breadboard.rl.harness.runners.base import RunnerDependencyError


def test_retains_real_native_failure_details_and_explicit_cause_traceback():
    failure = NativeProviderRequestFailure(
        ProviderRuntimeError("context limit", details={"status_code": 400, "code": "context_length_exceeded"}),
        request_body={"messages": [{"role": "user", "content": "request actually refused"}]},
    )
    try:
        try:
            raise failure
        except NativeProviderRequestFailure as cause:
            raise RunnerDependencyError("policy runtime invocation failed", code="policy_invoke_failed") from cause
    except RunnerDependencyError as error:
        evidence = _terminal_error_evidence(error)
    assert evidence["type"] == "RunnerDependencyError"
    assert evidence["message"] == "policy runtime invocation failed"
    assert "direct cause" in evidence["traceback"]
    assert "context limit" in evidence["traceback"]
    assert evidence["chain"][1]["type"] == "NativeProviderRequestFailure"
    assert evidence["chain"][1]["details"] == failure.details
    assert evidence["chain"][1]["request_body"] == dict(failure.request_body)
    assert evidence["chain"][1]["output_emitted"] is False


def test_retains_implicit_context_without_fabricating_native_fields():
    try:
        try:
            raise ValueError("underlying non-native failure")
        except ValueError:
            raise RunnerDependencyError("dependency", code="policy_invoke_failed")
    except RunnerDependencyError as error:
        evidence = _terminal_error_evidence(error)
    assert "During handling" in evidence["traceback"]
    assert [row["type"] for row in evidence["chain"]] == ["RunnerDependencyError", "ValueError"]
    assert all("request_body" not in row and "details" not in row for row in evidence["chain"])
