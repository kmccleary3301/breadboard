from __future__ import annotations

import json

import pytest

from breadboard.product.operations.model import from_exception


@pytest.mark.parametrize(
    ("error", "exit_code", "error_code", "message"),
    (
        (
            FileNotFoundError("sensitive /absolute/workspace/path"),
            3,
            "path_unavailable",
            "path is unavailable",
        ),
        (
            OSError("sensitive runtime detail"),
            4,
            "runtime_failure",
            "internal runtime failure",
        ),
    ),
)
def test_exception_results_preserve_classification_without_internal_details(
    error: Exception,
    exit_code: int,
    error_code: str,
    message: str,
) -> None:
    result = from_exception(["harness", "lock"], error, "harness.lock").as_dict()

    assert result["exit_code"] == exit_code
    assert result["error"]["error_code"] == error_code
    assert result["error"]["message"] == message
    assert "sensitive" not in json.dumps(result, sort_keys=True)


def test_validation_error_preserves_actionable_message() -> None:
    result = from_exception(
        ["session", "events"],
        ValueError("Bearer credentials require HTTPS"),
        "session.events",
    ).as_dict()

    assert result["exit_code"] == 2
    assert result["error"]["error_code"] == "invalid_state"
    assert result["error"]["message"] == "Bearer credentials require HTTPS"
