"""Behavioral wire-byte proof for named Pi environment footer guards."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import shutil

import pytest

from breadboard_engine.compaction_lanes.pi_environment_guards import (
    PINS, SOURCE, begin_pi_environment_observation,
    finish_pi_environment_observation, project_pi_environment,
)

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="Pi formatter needs Node")
PREFIX = "You are an expert coding assistant operating inside pi, a coding agent harness."
EPOCH = int(datetime(2026, 10, 12, 1, 4, 30, tzinfo=timezone.utc).timestamp() * 1000)


def evidence(version="0.57.1", *, cwd="/private/var/stock", zone="America/Los_Angeles", epoch=EPOCH):
    return {"version": version, "source_commit": PINS[version], "source": SOURCE,
            "cwd": cwd, "timezone": zone, "launch_epoch_ms": epoch,
            "prompt_build_upper_epoch_ms": epoch + 999}


def wire(date="Sunday, October 11, 2026 at 06:04:30 PM PDT", cwd="/private/var/stock", *, version="0.57.1", extra=""):
    label = "Current date and time: " if version == "0.57.1" else "Current date: "
    content = PREFIX + extra + "\n" + label + date + "\nCurrent working directory: " + cwd
    return json.dumps({"messages": [{"role": "system", "content": content},
                                   {"role": "user", "content": "unchanged /private/var/stock"}],
                       "tools": [{"description": "do not change paths /private/var/stock"}]},
                      ensure_ascii=False).encode()


@pytest.mark.parametrize("version", ["0.57.1", "0.73.1"])
def test_named_side_specific_footer_projection(version):
    stock_date = "Sunday, October 11, 2026 at 06:04:30 PM PDT" if version == "0.57.1" else "2026-10-11"
    bb_date = "Monday, October 12, 2026 at 01:04:33 AM UTC" if version == "0.57.1" else "2026-10-12"
    stock = wire(stock_date, version=version)
    bb = wire(bb_date, "/var/bb", version=version)
    left = evidence(version)
    right = evidence(version, cwd="/var/bb", zone="UTC", epoch=EPOCH+3000)
    if version == "0.73.1":
        right["declared_current_date"] = bb_date
    assert stock != bb
    projected_stock, records = project_pi_environment(stock, left, index=4, side="stock")
    projected_bb, _ = project_pi_environment(bb, right, index=4, side="bb")
    assert projected_stock == projected_bb
    assert len(records) == 2
    assert records[0]["raw_sha256"] == hashlib.sha256(stock).hexdigest()
    assert records[0]["index"] == 4
    assert records[0]["source_commit"] == PINS[version]
    cursor = 0
    rebuilt = b""
    for record in sorted(records, key=lambda r: r["start"]):
        assert stock[record["start"]:record["end"]].hex() == record["original_bytes"]
        rebuilt += stock[cursor:record["start"]] + bytes.fromhex(record["projected_bytes"])
        cursor = record["end"]
    rebuilt += stock[cursor:]
    assert rebuilt == projected_stock
    assert b"do not change paths /private/var/stock" in projected_stock
    assert b"unchanged /private/var/stock" in projected_stock


def test_nonfooter_prompt_and_wire_differences_remain_strict():
    obs = evidence()
    raw = wire(extra="\npackage /a/doc")
    changed = wire(extra="\npackage /b/doc")
    spaced = raw.replace(b'{"messages":', b'{ "messages" :', 1)
    projected, _ = project_pi_environment(raw, obs, index=0, side="stock")
    for other in (changed, spaced):
        assert project_pi_environment(other, obs, index=0, side="bb")[0] != projected


@pytest.mark.parametrize("date", [
    "Sunday, October 11, 2026 at 06:04:29 PM PDT",  # below launch bound
    "Sunday, October 11, 2026 at 06:04:31 PM PDT",  # above build bound
    "Monday, October 12, 2026 at 01:04:30 AM UTC",  # wrong timezone
    "Sunday October 11 2026 06:04:30 PM PDT",       # wrong pinned formatter
    "2026-10-11",                                 # wrong version format
])
def test_reject_unobserved_or_wrong_formatter_timestamp(date):
    with pytest.raises(ValueError):
        project_pi_environment(wire(date), evidence(), index=0, side="stock")


@pytest.mark.parametrize("cwd", ["/var/stock", "/private/var/other", "/private/var/stock/"])
def test_reject_unadmitted_cwd_including_alias(cwd):
    with pytest.raises(ValueError):
        project_pi_environment(wire(cwd=cwd), evidence(), index=0, side="stock")


@pytest.mark.parametrize("mutation", ["offslot", "duplicate", "notfinal", "role", "unqualified", "duplicate_member", "malformed"])
def test_reject_ambiguous_or_offslot_footer(mutation):
    raw = wire()
    body = json.loads(raw)
    if mutation == "offslot":
        body["messages"].reverse()
    elif mutation == "duplicate":
        body["messages"][0]["content"] += "\nCurrent working directory: /private/var/stock"
    elif mutation == "notfinal":
        body["messages"][0]["content"] += "\nextra"
    elif mutation == "role":
        body["messages"][0]["role"] = "user"
    elif mutation == "unqualified":
        body["messages"][0]["content"] = body["messages"][0]["content"].replace(PREFIX, "other prompt")
    elif mutation == "duplicate_member":
        raw = raw.replace(b'{"role": "system",', b'{"role": "system", "role": "system",', 1)
    elif mutation == "malformed":
        raw = raw[:-1]
    if mutation not in {"duplicate_member", "malformed"}:
        raw = json.dumps(body).encode()
    with pytest.raises(ValueError):
        project_pi_environment(raw, evidence(), index=0, side="stock")


def test_reject_duplicate_exact_carrier():
    body = json.loads(wire())
    body["messages"].append(deepcopy(body["messages"][0]))
    with pytest.raises(ValueError):
        project_pi_environment(json.dumps(body).encode(), evidence(), index=0, side="stock")


def test_pi073_declared_date_must_also_match_epoch_bounds():
    obs = evidence("0.73.1", cwd="/var/bb", zone="UTC")
    obs["declared_current_date"] = "2026-10-11"
    with pytest.raises(ValueError):
        project_pi_environment(wire("2026-10-11", "/var/bb", version="0.73.1"), obs, index=0, side="bb")


@pytest.mark.parametrize("change", [{"source_commit": "unapproved"},
                                    {"source": "unapproved"},
                                    {"prompt_build_upper_epoch_ms": EPOCH-1},
                                    {"launch_epoch_ms": "body-derived"}])
def test_reject_bad_authority_or_epoch_evidence(change):
    obs = evidence()
    obs.update(change)
    with pytest.raises(ValueError):
        project_pi_environment(wire(), obs, index=0, side="stock")


def test_observation_is_independent_of_prompt_body(tmp_path):
    obs = begin_pi_environment_observation("0.73.1", tmp_path, native=True)
    finish_pi_environment_observation(obs)
    assert obs["cwd"] == str(tmp_path)
    assert obs["timezone"] == "UTC"
    assert obs["launch_epoch_ms"] <= obs["prompt_build_upper_epoch_ms"]
    assert "declared_current_date" not in obs
    assert "prompt" not in obs


@pytest.mark.parametrize("version", ["0.57.1", "0.73.1"])
def test_exact_summary_prompt_is_not_an_environment_slot(version):
    from breadboard.rl.harness.native_stream_profiles import PI_SUMMARIZATION_SYSTEM_PROMPT
    raw = json.dumps({"messages": [{"role": "system", "content": PI_SUMMARIZATION_SYSTEM_PROMPT}],
                      "max_tokens": 800}).encode()
    assert project_pi_environment(raw, evidence(version), index=3, side="stock") == (raw, [])


def test_utf8_before_footer_keeps_exact_byte_offsets():
    raw = wire(extra="\nUTF8 café 東京")
    projected, records = project_pi_environment(raw, evidence(), index=2, side="stock")
    assert "UTF8 café 東京".encode() in projected
    for record in records:
        assert raw[record["start"]:record["end"]] == bytes.fromhex(record["original_bytes"])


def main_row(raw, *, path="/v1/chat/completions"):
    body = json.loads(raw)
    body["tools"] = []
    body["max_tokens"] = 2048
    encoded = json.dumps(body, ensure_ascii=False).encode()
    return {"json": body, "raw_body_bytes": encoded.hex(), "path": path, "method": "POST"}


@pytest.mark.parametrize("version", ["0.57.1", "0.73.1"])
def test_comparator_records_original_raw_spans_before_other_guards(version):
    from breadboard_engine.compaction_lanes.pi_requests import compare_recorded_requests
    stock_date = "Sunday, October 11, 2026 at 06:04:30 PM PDT" if version == "0.57.1" else "2026-10-11"
    bb_date = "Monday, October 12, 2026 at 01:04:33 AM UTC" if version == "0.57.1" else "2026-10-12"
    # Pi073 explicitly advertises removal of this one stock-only sentence.
    # It must run after footer observation, without changing recorded offsets.
    advertisement = (
        "\n\nIn addition to the tools above, you may have access to other custom tools depending on the project."
        if version == "0.73.1" else ""
    )
    stock = main_row(wire(stock_date, version=version, extra=advertisement))
    bb = main_row(wire(bb_date, "/var/bb", version=version))
    records = []
    passed, diffs = compare_recorded_requests(
        [stock], [bb], [], target_id="pi-r4@0.57.1" if version == "0.57.1" else "pi-r3@0.73.1",
        stock_environment=evidence(version),
        bb_environment=evidence(version, cwd="/var/bb", zone="UTC", epoch=EPOCH+3000),
        deviation_records=records,
    )
    assert passed and diffs == []
    assert records[0]["accepted"]
    for side, row in (("stock", stock), ("bb", bb)):
        raw = bytes.fromhex(row["raw_body_bytes"])
        assert len(records[0]["environment"][side]) == 2
        for change in records[0]["environment"][side]:
            assert change["raw_sha256"] == hashlib.sha256(raw).hexdigest()
            assert raw[change["start"]:change["end"]].hex() == change["original_bytes"]
    if version == "0.73.1":
        assert "pi_prompt_remove_exact_advertisement" in records[0]["names"]
        assert "Pi073PromptFooterDate" in records[0]["names"]
        assert "Pi073PromptFooterCwd" in records[0]["names"]


def test_comparator_admits_cwd_with_equal_observed_dates():
    from breadboard_engine.compaction_lanes.pi_requests import compare_recorded_requests
    stock = main_row(wire())
    bb = main_row(wire(cwd="/var/bb"))
    records = []
    passed, diffs = compare_recorded_requests(
        [stock], [bb], [], target_id="pi-r4@0.57.1",
        stock_environment=evidence(), bb_environment=evidence(cwd="/var/bb"),
        deviation_records=records,
    )
    assert passed and diffs == []
    assert records[0]["accepted"]
    assert "Pi057PromptFooterCwd" in records[0]["names"]


@pytest.mark.parametrize("mutation", ["whitespace", "system_rest"])
def test_comparator_does_not_hide_nonfooter_bytes(mutation):
    from breadboard_engine.compaction_lanes.pi_requests import compare_recorded_requests
    stock = main_row(wire())
    bb = main_row(wire("Monday, October 12, 2026 at 01:04:33 AM UTC", "/var/bb",
                       extra="\nextra system instruction" if mutation == "system_rest" else ""))
    if mutation == "whitespace":
        raw = bytes.fromhex(bb["raw_body_bytes"])
        bb["raw_body_bytes"] = raw.replace(b'{"messages":', b'{ "messages" :', 1).hex()
    records = []
    passed, diffs = compare_recorded_requests(
        [stock], [bb], [], target_id="pi-r4@0.57.1",
        stock_environment=evidence(),
        bb_environment=evidence(cwd="/var/bb", zone="UTC", epoch=EPOCH+3000),
        deviation_records=records,
    )
    assert not passed
    assert [diff["type"] for diff in diffs] == ["body_diff"]
    assert not records[0]["accepted"]
    assert records[0]["environment"]["stock"]


def test_comparator_route_only_mismatch_is_not_body_diff():
    from breadboard_engine.compaction_lanes.pi_requests import compare_recorded_requests
    stock = main_row(wire())
    bb = main_row(wire(), path="/chat/completions")
    passed, diffs = compare_recorded_requests(
        [stock], [bb], [], target_id="pi-r4@0.57.1",
        stock_environment=evidence(), bb_environment=evidence(),
    )
    assert not passed
    assert [diff["type"] for diff in diffs] == ["request_route"]
