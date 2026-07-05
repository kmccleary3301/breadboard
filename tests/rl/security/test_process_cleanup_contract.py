from __future__ import annotations

from breadboard.rl.security import validate_process_cleanup_before_verify


def test_process_cleanup_required_before_verify() -> None:
    assert validate_process_cleanup_before_verify(["reset", "verify"]) == [
        "process_cleanup event is required before verify"
    ]
    assert validate_process_cleanup_before_verify(["reset", "verify", "process_cleanup"]) == [
        "process_cleanup must occur before verify"
    ]
    assert validate_process_cleanup_before_verify(["reset", "process_cleanup", "verify"]) == []
