from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest

from agentic_coder_prototype.compilation.primitive_records import get_spec, validate_record
from scripts.e4_parity.adapters import pi_p5_l2_capture as builder


ACCEPTED_REPLAY_SHA256 = "567453e99e01d51ecc61dfe08ec7f003b9b638d8f7cbb885dce27fb8a2200c6f"
REQUIRED_RECORDS = {
    "extension_hook_execution_provider": "bb.extension_hook_execution.v1",
    "extension_hook_execution_session": "bb.extension_hook_execution.v1",
    "work_item_session_resume_fork": "bb.work_item.v1",
    "memory_compaction_plan": "bb.memory_compaction_plan.v1",
    "transcript_continuation_patch": "bb.transcript_continuation_patch.v1",
}


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_accepted_replay_records_are_byte_frozen_valid_evidence() -> None:
    accepted_bytes = builder.REPLAY_PATH.read_bytes()
    assert hashlib.sha256(accepted_bytes).hexdigest() == ACCEPTED_REPLAY_SHA256

    records = builder.load_accepted_replay_records()
    assert {name: record["schema_version"] for name, record in records.items()} == REQUIRED_RECORDS
    work_item = records["work_item_session_resume_fork"]
    assert validate_record(get_spec("bb.work_item.v1"), work_item) == work_item
    assert work_item["resume_policy"]["mode"] == "checkpoint"

    report = builder.build(write=False)
    assert report == {
        "ok": True,
        "config_id": builder.CONFIG_ID,
        "capture_mode": "reused",
        "validated_record_count": len(REQUIRED_RECORDS),
    }


@pytest.mark.parametrize(
    "path_name",
    ["REPLAY_PATH", "RAW_CAPTURE_PATH", "TARGET_PROBE_OUTPUT_PATH", "EVIDENCE_MANIFEST_PATH", "SUPPORT_CLAIM_PATH"],
)
def test_accepted_replay_one_byte_tampering_fails_closed(
    path_name: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = getattr(builder, path_name)
    data = bytearray(source.read_bytes())
    index = -1 if path_name == "REPLAY_PATH" else data.index(ACCEPTED_REPLAY_SHA256.encode()) if "MANIFEST" in path_name or "CLAIM" in path_name else 0
    data[index] = ord(" ") if index == -1 else (data[index] + 1) % 256
    tampered = tmp_path / source.name
    tampered.write_bytes(data)
    display_path = builder.display_path
    monkeypatch.setattr(builder, path_name, tampered)
    monkeypatch.setattr(builder, "display_path", lambda path: display_path(source) if path == tampered else display_path(path))

    with pytest.raises(RuntimeError, match="accepted replay (digest|evidence derivation chain) mismatch"):
        builder.load_accepted_replay_records()


@pytest.mark.parametrize(
    "regeneration",
    [
        lambda: builder.build(write=True),
        lambda: builder.build(write=False, recapture=True),
        lambda: builder.write_capture_replay_compare(),
        lambda: builder.capture(promote_accepted=True),
    ],
)
def test_work_item_v1_regeneration_paths_fail_closed(regeneration: Any) -> None:
    before = hashlib.sha256(builder.REPLAY_PATH.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match=re.escape(builder.IMMUTABLE_EVIDENCE_ERROR)):
        regeneration()
    assert hashlib.sha256(builder.REPLAY_PATH.read_bytes()).hexdigest() == before
    assert not hasattr(builder, "compile_replay_records")
