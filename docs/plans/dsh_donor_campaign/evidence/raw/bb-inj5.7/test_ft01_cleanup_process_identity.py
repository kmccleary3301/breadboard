"""Focused proof that FT-01 finalization accounts for the installed client."""
from __future__ import annotations

import copy
import json
from pathlib import Path

from validate_ft01_cleanup import CleanupValidationError, validate_attempt, validate_cleanup

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures/ft01_cleanup_process_identity_v1.json"


def _load_fixture() -> dict[str, object]:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def test_positive_finalization_represents_dead_owned_client() -> None:
    fixture = _load_fixture()
    validate_cleanup(fixture["valid_cleanup"], fixture["owned_processes"])
    started = {"started": True, "client_process": fixture["valid_cleanup"]["cases"][0]["client_process"]}
    validate_attempt(started)
    assert fixture["positive_assertion"].startswith("both client_process")


def test_negative_finalization_rejects_unrepresented_owned_client() -> None:
    fixture = _load_fixture()
    invalid = copy.deepcopy(fixture["valid_cleanup"])
    del invalid["cases"][0]["client_process"]
    try:
        validate_cleanup(invalid, fixture["owned_processes"])
    except CleanupValidationError as exc:
        assert "schema" in str(exc) or "client_process" in str(exc)
    else:
        raise AssertionError("unrepresented owned client must fail finalization")


def test_unstarted_attempt_must_explicitly_record_no_client() -> None:
    validate_attempt({"started": False, "client_process": None})
    try:
        validate_attempt({"started": False})
    except CleanupValidationError as exc:
        assert "client_process" in str(exc)
    else:
        raise AssertionError("omitted client process must fail closed")
    try:
        validate_attempt({"started": True})
    except CleanupValidationError as exc:
        assert "client_process" in str(exc)
    else:
        raise AssertionError("started attempt must identify its owned client")


if __name__ == "__main__":
    test_positive_finalization_represents_dead_owned_client()
    test_negative_finalization_rejects_unrepresented_owned_client()
    test_unstarted_attempt_must_explicitly_record_no_client()
    print("PASS: FT-01 client process identity")
