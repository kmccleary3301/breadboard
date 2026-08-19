from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Iterator

import pytest
import yaml
from jsonschema import Draft202012Validator, RefResolver

from scripts.authoring import validate_lane as validator
from scripts.e4_parity import lane_definitions
from scripts.e4_parity.validators import registries
from tests.test_e4_c4_chain_validation import _build_chain


ACCEPTED_LANE_ID = "oh_my_pi_p3_1_effective_config_graph_compiler"
SCHEMA_DIR = Path(__file__).resolve().parents[2] / "contracts" / "kernel" / "schemas"
SCHEMA_PATH = SCHEMA_DIR / "bb.lane_validation_report.v1.schema.json"
EXAMPLE_PATH = (
    Path(__file__).resolve().parents[2] / "contracts" / "kernel" / "examples" / "lane_validation_report_minimal.json"
)
EXPECTED_CHECK_IDS = (
    "lane_def_schema_valid",
    "adapter_registered",
    "translator_registered",
    "comparator_registered",
    "capture_inputs_exist",
    "artifacts_root_valid",
    "inventory_row_consistent",
    "assertion_ids_unique",
    "claim_scope_complete",
    "reverify_command_executable",
    "accepted_artifact_hashes_fresh",
    "metadata_non_normative",
)


def _run_cli(lane_arg: str, capsys: pytest.CaptureFixture[str]) -> tuple[int, dict[str, Any]]:
    exit_code = validator.main(["--lane", lane_arg])
    captured = capsys.readouterr()
    assert captured.err == ""
    return exit_code, json.loads(captured.out)


def _assert_report_shape(report: dict[str, Any]) -> None:
    assert set(report) == {"schema_version", "lane_id", "lane_def_path", "generated_at_utc", "checks", "ok"}
    assert report["schema_version"] == "bb.lane_validation_report.v1"
    assert isinstance(report["lane_id"], str) and report["lane_id"]
    assert isinstance(report["lane_def_path"], str) and report["lane_def_path"]
    assert report["generated_at_utc"] == "2026-07-09T00:00:00Z"
    assert isinstance(report["ok"], bool)
    checks = report["checks"]
    assert [check["check_id"] for check in checks] == list(EXPECTED_CHECK_IDS)
    for check in checks:
        assert set(check) == {"check_id", "status", "detail"}
        assert check["status"] in {"passed", "failed", "skipped"}
        assert isinstance(check["detail"], str) and check["detail"]


def _checks_by_id(report: dict[str, Any]) -> dict[str, dict[str, str]]:
    return {check["check_id"]: check for check in report["checks"]}


def _lane_report_validator() -> Draft202012Validator:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    store: dict[str, dict[str, Any]] = {}
    for schema_path in SCHEMA_DIR.glob("*.json"):
        loaded = json.loads(schema_path.read_text(encoding="utf-8"))
        schema_id = loaded.get("$id")
        if isinstance(schema_id, str):
            store[schema_id] = loaded
        store[schema_path.name] = loaded
    resolver = RefResolver(
        base_uri=SCHEMA_DIR.resolve().as_uri() + "/",
        referrer=schema,
        store=store,
    )
    return Draft202012Validator(schema, resolver=resolver)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.fixture
def hermetic_accepted_lane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[dict[str, Path | str]]:
    chain = _build_chain(tmp_path, checkout_local_sources=True)
    repo_root = Path(chain["repo"])

    schema_dir = repo_root / "contracts" / "kernel" / "schemas"
    schema_dir.mkdir(parents=True)
    for schema_name in (
        "bb.e4.lane_def.v2.schema.json",
        "bb.e4.common.v1.schema.json",
        "bb.kernel.common.v1.schema.json",
    ):
        shutil.copyfile(SCHEMA_DIR / schema_name, schema_dir / schema_name)

    registry_dir = repo_root / "contracts" / "kernel" / "registries"
    _write_json(
        registry_dir / "e4_adapters.v1.json",
        {
            "registry_id": "e4_adapters",
            "entries": [
                {
                    "id": "oh_my_pi_compiler_capture",
                    "status": "active",
                    "metadata": {"kind": "capture_adapter"},
                },
                {
                    "id": "identity",
                    "status": "active",
                    "metadata": {
                        "kind": "translator",
                        "impl": "scripts.e4_parity.adapters.identity:translate",
                    },
                },
                {
                    "id": "oh_my_pi_stored_report_replay",
                    "status": "active",
                    "metadata": {"kind": "comparator"},
                },
            ],
        },
    )

    source = validator.ROOT / "config" / "e4_lanes" / f"{ACCEPTED_LANE_ID}.yaml"
    lane = yaml.safe_load(source.read_text(encoding="utf-8"))
    capture_path = Path(chain["capture"])
    lane_id = json.loads(capture_path.read_text(encoding="utf-8"))["lane_id"]
    support_claim_path = Path(chain["support_claim"])
    evidence_manifest_path = Path(chain["evidence_manifest"])
    evidence_root = capture_path.parent
    reverify_script = repo_root / "scripts" / "validate_e4_c4_chain.py"
    reverify_script.parent.mkdir(parents=True)
    reverify_script.write_text("raise SystemExit('fixture command is declarative only')\n", encoding="utf-8")

    lane.update(
        {
            "lane_id": lane_id,
            "config_id": chain["config_id"],
            "target_version": "codex-cli 0.139.0",
            "artifacts_root": evidence_root.relative_to(repo_root).as_posix(),
            "run": {
                "run_id": "20260630_codex_gpt55_capture_probe",
                "provider_model": "gpt-5.5",
                "sandbox_mode": "read-only",
            },
        }
    )
    lane["capture"]["inputs"] = [capture_path.relative_to(repo_root).as_posix()]
    lane["reverify_command"]["argv"] = [
        ".venv/bin/python",
        reverify_script.relative_to(repo_root).as_posix(),
        "--config-id",
        str(chain["config_id"]),
        "--support-claim",
        support_claim_path.relative_to(repo_root).as_posix(),
        "--evidence-manifest",
        evidence_manifest_path.relative_to(repo_root).as_posix(),
        "--json-out",
        "artifacts/conformance/node_gate/hermetic_lane_validation.json",
        "--check-only",
    ]

    lane_dir = repo_root / "config" / "e4_lanes"
    lane_path = lane_dir / f"{lane_id}.yaml"
    lane_dir.mkdir(parents=True)
    lane_path.write_text(yaml.safe_dump(lane, sort_keys=False), encoding="utf-8")

    inventory_path = repo_root / "docs" / "conformance" / "e4_lane_inventory.json"
    score_subledger_path = repo_root / "docs" / "conformance" / "_fixtures" / "score_subledger.json"
    _write_json(score_subledger_path, {"score_rows": []})
    monkeypatch.setattr(validator, "ROOT", repo_root)
    monkeypatch.setattr(validator, "WORKSPACE", repo_root.parent)
    monkeypatch.setattr(validator, "DEFAULT_LANE_DEF_DIR", lane_dir)
    monkeypatch.setattr(validator, "DEFAULT_INVENTORY_PATH", inventory_path)
    monkeypatch.setattr(lane_definitions, "ROOT", repo_root)
    monkeypatch.setattr(lane_definitions, "DEFAULT_LANE_DEF_DIR", lane_dir)
    monkeypatch.setattr(registries, "REGISTRY_DIR", registry_dir)
    monkeypatch.setattr(validator.generate_lane_inventory, "ROOT", repo_root)
    monkeypatch.setattr(validator.generate_lane_inventory, "WORKSPACE", repo_root.parent)
    monkeypatch.setattr(
        validator.generate_lane_inventory,
        "SCORE_SUBLEDGER_PATH",
        score_subledger_path,
    )
    monkeypatch.setattr(validator.generate_lane_inventory, "_SCORE_SUBLEDGER_CACHE", None)
    lane_definitions._validator.cache_clear()
    registries.load_registry.cache_clear()

    loaded_lane = lane_definitions.load_lane_def(lane_path)
    inventory_row = validator.generate_lane_inventory.lane_inventory_row(loaded_lane)
    _write_json(inventory_path, {"lanes": [inventory_row]})

    try:
        yield {
            **chain,
            "lane": lane_path,
            "ledger": evidence_root / "atomic_feature_ledger.json",
        }
    finally:
        lane_definitions._validator.cache_clear()
        registries.load_registry.cache_clear()
        validator.generate_lane_inventory._SCORE_SUBLEDGER_CACHE = None


def test_inventory_check_rejects_retained_evidence_status_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    generated = {"lane_id": "demo", "evidence_status": "accepted"}
    monkeypatch.setattr(
        validator.generate_lane_inventory,
        "lane_inventory_row",
        lambda _lane_def: generated,
    )

    check = validator._inventory_check(
        {},
        {**generated, "evidence_status": "blocked"},
    )

    assert check["status"] == "failed"
    assert check["detail"] == "inventory mismatches: evidence_status"


@pytest.mark.parametrize(
    ("ok", "failed_check"),
    [
        pytest.param(True, True, id="ok-with-failed-check"),
        pytest.param(False, False, id="not-ok-without-failed-check"),
    ],
)
def test_lane_report_schema_rejects_ok_values_inconsistent_with_failed_checks(
    ok: bool,
    failed_check: bool,
) -> None:
    report = json.loads(EXAMPLE_PATH.read_text(encoding="utf-8"))
    report["ok"] = ok
    if failed_check:
        report["checks"][0]["status"] = "failed"

    errors = list(_lane_report_validator().iter_errors(report))

    assert len(errors) == 1
    assert list(errors[0].absolute_path) == ["checks"]


def test_hermetic_accepted_lane_cli_emits_ok_v1_report_with_exact_checks(
    hermetic_accepted_lane: dict[str, Path | str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    exit_code, report = _run_cli(str(hermetic_accepted_lane["lane"]), capsys)

    _assert_report_shape(report)
    assert exit_code == 0
    assert report["ok"] is True
    assert all(check["status"] == "passed" for check in report["checks"])


def test_real_accepted_lane_without_workspace_authority_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("BB_WORKSPACE_ROOT", raising=False)

    exit_code, report = _run_cli(ACCEPTED_LANE_ID, capsys)

    _assert_report_shape(report)
    checks = _checks_by_id(report)
    assert exit_code == 1
    assert report["ok"] is False
    assert checks["accepted_artifact_hashes_fresh"]["status"] == "failed"
    assert "BB_WORKSPACE_ROOT is required for workspace evidence references" in checks[
        "accepted_artifact_hashes_fresh"
    ]["detail"]


def test_hermetic_lane_with_missing_capture_input_fails_only_that_check(
    hermetic_accepted_lane: dict[str, Path | str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    lane_path = Path(hermetic_accepted_lane["lane"])
    payload = yaml.safe_load(lane_path.read_text(encoding="utf-8"))
    missing_input = "docs/conformance/e4_target_support/__missing_capture_input__.json"
    payload["capture"]["inputs"] = [missing_input]
    lane_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    exit_code, report = _run_cli(str(lane_path), capsys)

    _assert_report_shape(report)
    checks = _checks_by_id(report)
    assert exit_code == 1
    assert report["ok"] is False
    assert checks["capture_inputs_exist"]["status"] == "failed"
    assert missing_input in checks["capture_inputs_exist"]["detail"]
    assert {
        check_id: check["status"]
        for check_id, check in checks.items()
        if check_id != "capture_inputs_exist"
    } == {check_id: "passed" for check_id in EXPECTED_CHECK_IDS if check_id != "capture_inputs_exist"}


@pytest.mark.parametrize("artifact_key", ["capture", "ledger"])
def test_hermetic_lane_detects_accepted_artifact_integrity_mutation(
    hermetic_accepted_lane: dict[str, Path | str],
    capsys: pytest.CaptureFixture[str],
    artifact_key: str,
) -> None:
    artifact_path = Path(hermetic_accepted_lane[artifact_key])
    payload = json.loads(artifact_path.read_text(encoding="utf-8"))
    payload["integrity_test_mutation"] = artifact_key
    _write_json(artifact_path, payload)

    exit_code, report = _run_cli(str(hermetic_accepted_lane["lane"]), capsys)

    _assert_report_shape(report)
    checks = _checks_by_id(report)
    assert exit_code == 1
    assert report["ok"] is False
    assert checks["accepted_artifact_hashes_fresh"]["status"] == "failed"
    assert "hash mismatch" in checks["accepted_artifact_hashes_fresh"]["detail"]
    assert {
        check_id: check["status"]
        for check_id, check in checks.items()
        if check_id != "accepted_artifact_hashes_fresh"
    } == {
        check_id: "passed"
        for check_id in EXPECTED_CHECK_IDS
        if check_id != "accepted_artifact_hashes_fresh"
    }



def _lane_def_with_assertion_ids(
    *,
    acceptance_ids: list[str],
    compare_ids: list[str],
) -> dict[str, Any]:
    return {
        "acceptance": {
            "assertions": [
                {"id": assertion_id, "description": f"acceptance assertion {assertion_id}"}
                for assertion_id in acceptance_ids
            ],
        },
        "compare": {
            "config": {
                "assertions": [
                    {"id": assertion_id, "description": f"compare assertion {assertion_id}"}
                    for assertion_id in compare_ids
                ],
            },
        },
    }


def test_assertion_ids_check_rejects_duplicate_acceptance_assertion_ids() -> None:
    check = validator._assertion_ids_check(
        _lane_def_with_assertion_ids(acceptance_ids=["same_assertion", "same_assertion"], compare_ids=[])
    )

    assert check["check_id"] == "assertion_ids_unique"
    assert check["status"] == "failed"
    assert "same_assertion" in check["detail"]


def test_assertion_ids_check_rejects_duplicate_compare_assertion_ids() -> None:
    check = validator._assertion_ids_check(
        _lane_def_with_assertion_ids(acceptance_ids=[], compare_ids=["same_assertion", "same_assertion"])
    )

    assert check["check_id"] == "assertion_ids_unique"
    assert check["status"] == "failed"
    assert "same_assertion" in check["detail"]


def test_assertion_ids_check_allows_same_id_in_acceptance_and_compare_namespaces() -> None:
    check = validator._assertion_ids_check(
        _lane_def_with_assertion_ids(acceptance_ids=["shared_assertion"], compare_ids=["shared_assertion"])
    )

    assert check == {
        "check_id": "assertion_ids_unique",
        "status": "passed",
        "detail": "2 explicit assertion id(s) are unique",
    }

def test_cli_metadata_check_reports_the_metadata_sentinel_helper_result(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(
        validator,
        "assert_lane_metadata_non_normative",
        lambda: {"lane_id": "patched_metadata_sentinel"},
    )

    exit_code, report = _run_cli("not_a_lane_definition", capsys)

    _assert_report_shape(report)
    assert exit_code == 1
    assert _checks_by_id(report)["metadata_non_normative"] == {
        "check_id": "metadata_non_normative",
        "status": "passed",
        "detail": "metadata sentinel passed for patched_metadata_sentinel",
    }


@pytest.mark.parametrize(
    ("field_path", "replacement", "expected_pointer"),
    [
        pytest.param(("capture",), None, "/capture", id="capture-block"),
        pytest.param(("normalize",), None, "/normalize", id="normalize-block"),
        pytest.param(("normalize", "mode"), None, "/normalize/mode", id="normalize-mode"),
        pytest.param(("replay",), None, "/replay", id="replay-block"),
        pytest.param(("replay", "mode"), None, "/replay/mode", id="replay-mode"),
        pytest.param(("replay", "artifacts"), None, "/replay/artifacts", id="replay-artifacts"),
        pytest.param(("replay", "artifacts"), [], "/replay/artifacts", id="empty-replay-artifacts"),
        pytest.param(("compare",), None, "/compare", id="compare-block"),
        pytest.param(("claim",), None, "/claim", id="claim-block"),
    ],
)
def test_cli_rejects_implicit_or_empty_stage_declarations_with_field_pointer(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    field_path: tuple[str, ...],
    replacement: Any,
    expected_pointer: str,
) -> None:
    source = validator.ROOT / "config" / "e4_lanes" / f"{ACCEPTED_LANE_ID}.yaml"
    payload = yaml.safe_load(source.read_text(encoding="utf-8"))
    container = payload
    for field in field_path[:-1]:
        container = container[field]
    if replacement is None:
        container.pop(field_path[-1])
    else:
        container[field_path[-1]] = replacement
    lane_path = tmp_path / f"invalid_{'_'.join(field_path)}.yaml"
    lane_path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    exit_code, report = _run_cli(str(lane_path), capsys)

    _assert_report_shape(report)
    checks = _checks_by_id(report)
    assert exit_code == 1
    assert report["ok"] is False
    assert checks["lane_def_schema_valid"]["status"] == "failed"
    assert expected_pointer in checks["lane_def_schema_valid"]["detail"]
    assert all(checks[check_id]["status"] == "skipped" for check_id in EXPECTED_CHECK_IDS[1:-1])
