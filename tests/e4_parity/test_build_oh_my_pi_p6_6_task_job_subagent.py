from __future__ import annotations

import hashlib
import importlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

import pytest
from jsonschema import Draft202012Validator


MODULE_NAME = "scripts.e4_parity.build_oh_my_pi_p6_6_task_job_subagent"
if importlib.util.find_spec(MODULE_NAME) is None:
    pytest.skip("P6.6 task/job/subagent builder is supplied by Main", allow_module_level=True)

builder = importlib.import_module(MODULE_NAME)


GENERATED_AT = "2026-07-03T00:00:00Z"
EXPECTED_LANE_ID = "oh_my_pi_p6_6_task_job_subagent"
EXPECTED_CONFIG_ID = "oh_my_pi_p6_6_task_job_subagent_v1"
EXPECTED_CLAIM_ID = "oh_my_pi_p6_6_task_job_subagent_v1_c4_support_claim"
REQUIRED_COMPARATOR_ASSERTIONS = {
    "scope_matches_exact_claim",
    "commands_exit_zero",
    "breadboard_work_items_schema_valid",
    "target_joined_subagent_lifecycle_observed",
    "target_detached_subagent_lifecycle_observed",
    "background_job_lifecycle_observed",
    "cancel_lifecycle_observed",
    "job_manager_only_evidence_rejected",
    "provider_network_scope_limited",
}
REQUIRED_EVIDENCE_ROLES = {
    "freeze_manifest",
    "capture_ref",
    "joined_subagent_target_capture",
    "detached_subagent_target_capture",
    "work_item_ref",
    "work_item_replay",
    "task_job_subagent_comparator",
    "replay_ref",
    "comparator_ref",
    "parity_results",
    "secret_scan_report",
    "validator_output",
    "support_claim_ref",
    "atomic_feature_ledger",
    "target_probe_output",
}


class BuilderSandbox:
    def __init__(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        self.workspace = tmp_path
        self.repo_root = tmp_path / "breadboard_repo_integration_main_20260326"
        self.source_root = self.workspace / "docs_tmp" / "phase_15" / "source_freezes" / "oh_my_pi_main_latest"
        self.source_freeze = self.workspace / "docs_tmp" / "phase_15" / "source_freezes" / "oh_my_pi_main_5356713e_freeze_provenance.json"
        self.ledger_path = self.workspace / "docs_tmp" / "phase_15" / "BB_E4_ATOMIC_FEATURE_LEDGER_SEED.json"
        self.lane_dir = self.repo_root / "docs" / "conformance" / "e4_target_support" / builder.LANE_ID
        self.raw_dir = self.lane_dir / "raw"
        self.joined_capture_path = self.lane_dir / "joined_subagent_target_capture.json"
        self.detached_capture_path = self.lane_dir / "detached_subagent_target_capture.json"
        self.work_items_path = self.lane_dir / "work_items.json"
        self.work_item_replay_path = self.lane_dir / "work_item_replay.json"

        replacements = {
            "ROOT": self.repo_root,
            "WORKSPACE": self.workspace,
            "SOURCE_ROOT": self.source_root,
            "SOURCE_FREEZE": self.source_freeze,
            "LEDGER_PATH": self.ledger_path,
            "FREEZE_MANIFEST_PATH": self.repo_root / "config" / "e4_target_freeze_manifest.yaml",
            "AGENT_CONFIG_PATH": self.repo_root / "agent_configs" / "misc" / "oh_my_pi_p6_6_task_job_subagent_v1.yaml",
            "LANE_DIR": self.lane_dir,
            "RAW_DIR": self.raw_dir,
            "TARGET_HOME": self.lane_dir / "target_home",
            "PROBE_SCRIPT_PATH": self.lane_dir / f"{builder.LANE_ID}_probe.mjs",
            "RAW_CAPTURE_PATH": self.lane_dir / "raw_capture_manifest.json",
            "TARGET_PROBE_PATH": self.lane_dir / "target_probe_output.json",
            "SETUP_REPORT_PATH": self.lane_dir / "target_setup_and_capture_report.json",
            "JOINED_CAPTURE_PATH": self.joined_capture_path,
            "DETACHED_CAPTURE_PATH": self.detached_capture_path,
            "WORK_ITEMS_PATH": self.work_items_path,
            "WORK_ITEM_PATH": self.work_items_path,
            "WORK_ITEM_REPLAY_PATH": self.work_item_replay_path,
            "REPLAY_PATH": self.lane_dir / "bb_replay_result.json",
            "COMPARATOR_PATH": self.lane_dir / "comparator_report.json",
            "PARITY_PATH": self.lane_dir / "parity_results.json",
            "SECRET_SCAN_PATH": self.lane_dir / "secret_scan_report.json",
            "PREVALIDATION_PATH": self.lane_dir / "prevalidation_report.json",
            "WORK_ITEM_SCHEMA_PATH": self.repo_root / "contracts" / "kernel" / "schemas" / "bb.work_item.v1.schema.json",
            "SUPPORT_CLAIM_PATH": self.repo_root / "docs" / "conformance" / "support_claims" / f"{builder.CLAIM_ID}.json",
            "EVIDENCE_MANIFEST_PATH": self.repo_root / "docs" / "conformance" / "support_claims" / f"{builder.CLAIM_ID.replace('_support_claim', '_evidence_manifest')}.json",
        }
        for name, value in replacements.items():
            monkeypatch.setattr(builder, name, value, raising=False)

    def write_text(self, path: Path, content: str = "fixture\n") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_json(self, path: Path, payload: Mapping[str, Any] | list[Any]) -> None:
        self.write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def seed_source_files(self) -> None:
        source_ref_paths = list(
            getattr(
                builder,
                "SOURCE_REF_PATHS",
                [
                    "packages/coding-agent/src/async/job-manager.ts",
                    "packages/coding-agent/src/agent/task-tool.ts",
                    "packages/coding-agent/src/agent/subagent.ts",
                    "packages/coding-agent/src/tools/task.ts",
                ],
            )
        )
        for relative_path in source_ref_paths:
            self.write_text(self.source_root / str(relative_path), f"// {relative_path}\n")

    def seed_capture_inputs(self, *, job_manager_only: bool = False, missing_lifecycle: str | None = None) -> list[dict[str, Any]]:
        self.seed_source_files()
        self.write_json(builder.JOINED_CAPTURE_PATH, self.joined_capture_payload())
        self.write_json(builder.DETACHED_CAPTURE_PATH, self.detached_capture_payload())
        self.write_json(builder.TARGET_PROBE_PATH, self.target_probe_payload(job_manager_only=job_manager_only, missing_lifecycle=missing_lifecycle))
        self.write_json(builder.SETUP_REPORT_PATH, {"schema_version": "test.setup", "ok": True})
        self.write_text(builder.PROBE_SCRIPT_PATH, "console.log('p6.6 probe')\n")
        self.write_text(builder.AGENT_CONFIG_PATH, "provider: no-provider\nsandbox_mode: read-only\n")
        self.write_text(builder.LANE_DIR / "target_fixture_workspace" / ".omp" / "SYSTEM.md", "system fixture\n")

        reports: list[dict[str, Any]] = []
        for spec in self.command_specs():
            name = str(spec["name"])
            stdout_path = builder.RAW_DIR / f"{name}.stdout.txt"
            stderr_path = builder.RAW_DIR / f"{name}.stderr.txt"
            report_path = builder.RAW_DIR / f"{name}.command_report.json"
            stdout = self.command_stdout(name, job_manager_only=job_manager_only, missing_lifecycle=missing_lifecycle)
            self.write_text(stdout_path, stdout)
            self.write_text(stderr_path, "")
            report = {
                "schema_version": "bb.e4.command_report.v1",
                "name": name,
                "argv": list(spec.get("argv", ["omp", name])),
                "cwd": str(self.source_root),
                "exit_code": 0,
                "report_path": display_path(report_path),
                "stdout_path": display_path(stdout_path),
                "stderr_path": display_path(stderr_path),
                "stdout_sha256": sha256_file(stdout_path),
                "stderr_sha256": sha256_file(stderr_path),
                "description": spec.get("description"),
            }
            self.write_json(report_path, report)
            reports.append(report)
        return reports

    def seed_support_inputs(self) -> None:
        self.write_json(self.source_freeze, {"commit": builder.UPSTREAM_COMMIT, "repo": builder.UPSTREAM_REPO})
        self.write_json(self.ledger_path, {"rows": []})
        self.write_text(
            builder.FREEZE_MANIFEST_PATH,
            "e4_configs:\n"
            f"  {builder.CONFIG_ID}:\n"
            f"    id: {builder.CONFIG_ID}\n"
            f"    lane_id: {builder.LANE_ID}\n"
            "    target_family: oh_my_pi\n"
            "    evidence_tier: C4\n",
        )

    def command_specs(self) -> Sequence[Mapping[str, Any]]:
        return list(
            getattr(
                builder,
                "COMMAND_SPECS",
                (
                    {"name": "omp_joined_subagent_capture", "argv": ["omp", "task", "joined"]},
                    {"name": "omp_detached_subagent_capture", "argv": ["omp", "task", "detached"]},
                    {"name": "omp_job_list_poll_cancel_capture", "argv": ["omp", "job", "poll"]},
                ),
            )
        )

    def command_stdout(self, name: str, *, job_manager_only: bool, missing_lifecycle: str | None) -> str:
        if "probe" in name or "json" in name:
            return json.dumps(self.target_probe_payload(job_manager_only=job_manager_only, missing_lifecycle=missing_lifecycle), sort_keys=True) + "\n"
        if "joined" in name and missing_lifecycle != "joined":
            return json.dumps(self.joined_capture_payload(), sort_keys=True) + "\n"
        if ("detached" in name or "cancel" in name or "job" in name) and missing_lifecycle != "detached":
            return json.dumps(self.detached_capture_payload(), sort_keys=True) + "\n"
        return "16.2.13\n" if "version" in name else "p6.6 target lifecycle fixture\n"

    def joined_capture_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "bb.e4.oh_my_pi_p6_6_joined_subagent_capture.v1",
            "capture_id": "joined-subagent-target-capture",
            "lifecycle_mode": "joined",
            "target_family": builder.TARGET_FAMILY,
            "target_version": builder.TARGET_VERSION,
            "events": [
                {"event_kind": "task_tool_invoked", "work_item_id": "wi-joined-subagent", "task_id": "joined-task", "subagent_id": "joined-subagent", "status": "queued"},
                {"event_kind": "subagent_spawned", "work_item_id": "wi-joined-subagent", "task_id": "joined-task", "subagent_id": "joined-subagent", "status": "running"},
                {"event_kind": "subagent_completed", "work_item_id": "wi-joined-subagent", "task_id": "joined-task", "subagent_id": "joined-subagent", "status": "completed"},
                {"event_kind": "joined_result_returned", "work_item_id": "wi-joined-subagent", "task_id": "joined-task", "subagent_id": "joined-subagent", "status": "completed"},
            ],
        }

    def detached_capture_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "bb.e4.oh_my_pi_p6_6_detached_subagent_capture.v1",
            "capture_id": "detached-subagent-target-capture",
            "lifecycle_mode": "detached",
            "target_family": builder.TARGET_FAMILY,
            "target_version": builder.TARGET_VERSION,
            "events": [
                {"event_kind": "task_tool_invoked", "work_item_id": "wi-detached-subagent", "task_id": "detached-task", "subagent_id": "detached-subagent", "job_id": "job-detached-1", "status": "queued"},
                {"event_kind": "subagent_spawned", "work_item_id": "wi-detached-subagent", "task_id": "detached-task", "subagent_id": "detached-subagent", "job_id": "job-detached-1", "status": "running"},
                {"event_kind": "background_job_created", "work_item_id": "wi-detached-job", "task_id": "detached-task", "subagent_id": "detached-subagent", "job_id": "job-detached-1", "status": "running"},
                {"event_kind": "job_list_returned", "work_item_id": "wi-detached-job", "task_id": "detached-task", "subagent_id": "detached-subagent", "job_id": "job-detached-1", "status": "running"},
                {"event_kind": "job_poll_running", "work_item_id": "wi-detached-job", "task_id": "detached-task", "subagent_id": "detached-subagent", "job_id": "job-detached-1", "status": "running"},
                {"event_kind": "job_cancel_requested", "work_item_id": "wi-detached-job", "task_id": "detached-task", "subagent_id": "detached-subagent", "job_id": "job-detached-1", "status": "cancelled"},
                {"event_kind": "subagent_cancelled", "work_item_id": "wi-detached-subagent", "task_id": "detached-task", "subagent_id": "detached-subagent", "job_id": "job-detached-1", "status": "cancelled"},
            ],
        }

    def target_probe_payload(self, *, job_manager_only: bool = False, missing_lifecycle: str | None = None) -> dict[str, Any]:
        captures = [] if job_manager_only else []
        if not job_manager_only and missing_lifecycle != "joined":
            captures.append(
                {
                    "capture_id": "joined-subagent-target-capture",
                    "capture_kind": "joined_subagent",
                    "path": display_path(builder.JOINED_CAPTURE_PATH),
                    "sha256": sha256_file(builder.JOINED_CAPTURE_PATH),
                    "observed_events": ["task_tool_invoked", "subagent_spawned", "subagent_completed", "joined_result_returned"],
                }
            )
        if not job_manager_only and missing_lifecycle != "detached":
            captures.append(
                {
                    "capture_id": "detached-subagent-target-capture",
                    "capture_kind": "detached_subagent",
                    "path": display_path(builder.DETACHED_CAPTURE_PATH),
                    "sha256": sha256_file(builder.DETACHED_CAPTURE_PATH),
                    "observed_events": ["task_tool_invoked", "subagent_spawned", "background_job_created", "job_list_returned", "job_poll_running", "job_cancel_requested", "subagent_cancelled"],
                }
            )
        observations = [
            {
                "observation_id": "job-manager-only-list-poll-cancel",
                "evidence_source": "job_manager_only",
                "lifecycle_mode": "detached",
                "work_item_id": "wi-job-manager-only",
                "task_kind": "background",
                "task_id": None,
                "subagent_id": None,
                "job_id": "job-manager-only-1",
                "status": "cancelled",
                "observed_events": ["job_list_returned", "job_poll_running", "job_cancel_requested"],
                "sufficient_for_promotion": False,
            }
        ]
        if not job_manager_only:
            if missing_lifecycle != "joined":
                observations.append(
                    {
                        "observation_id": "joined-subagent-completed",
                        "evidence_source": "target_subagent_lifecycle",
                        "source_capture_id": "joined-subagent-target-capture",
                        "lifecycle_mode": "joined",
                        "work_item_id": "wi-joined-subagent",
                        "task_kind": "subagent",
                        "task_id": "joined-task",
                        "subagent_id": "joined-subagent",
                        "job_id": None,
                        "status": "completed",
                        "observed_events": ["task_tool_invoked", "subagent_spawned", "subagent_completed", "joined_result_returned"],
                        "sufficient_for_promotion": True,
                    }
                )
            if missing_lifecycle != "detached":
                observations.extend(
                    [
                        {
                            "observation_id": "detached-subagent-cancelled",
                            "evidence_source": "target_subagent_lifecycle",
                            "source_capture_id": "detached-subagent-target-capture",
                            "lifecycle_mode": "detached",
                            "work_item_id": "wi-detached-subagent",
                            "task_kind": "subagent",
                            "task_id": "detached-task",
                            "subagent_id": "detached-subagent",
                            "job_id": "job-detached-1",
                            "status": "cancelled",
                            "observed_events": ["task_tool_invoked", "subagent_spawned", "background_job_created", "job_list_returned", "job_poll_running", "job_cancel_requested", "subagent_cancelled"],
                            "sufficient_for_promotion": True,
                        },
                        {
                            "observation_id": "detached-background-job-cancelled",
                            "evidence_source": "target_subagent_lifecycle",
                            "source_capture_id": "detached-subagent-target-capture",
                            "lifecycle_mode": "detached",
                            "work_item_id": "wi-detached-job",
                            "task_kind": "background",
                            "task_id": "detached-task",
                            "subagent_id": "detached-subagent",
                            "job_id": "job-detached-1",
                            "status": "cancelled",
                            "observed_events": ["background_job_created", "job_list_returned", "job_poll_running", "job_cancel_requested", "subagent_cancelled"],
                            "sufficient_for_promotion": True,
                        },
                    ]
                )
        return {
            "schema_version": "bb.e4.oh_my_pi_p6_6_task_job_subagent_report.v1",
            "target_family": builder.TARGET_FAMILY,
            "target_version": builder.TARGET_VERSION,
            "config_id": builder.CONFIG_ID,
            "lane_id": builder.LANE_ID,
            "run_id": builder.RUN_ID,
            "provider_model": builder.PROVIDER_MODEL,
            "sandbox_mode": builder.SANDBOX_MODE,
            "provider_authenticated_capture": True,
            "provider_dispatch_observed": True,
            "provider_parity_claimed": False,
            "network_observed": True,
            "fetch_events": ["anthropic-messages"],
            "fetch_event_count": 1,
            "job_manager_only_evidence": job_manager_only,
            "target_captures": captures,
            "work_item_observations": observations,
            "explicit_exclusions": [
                "job-manager list/poll/cancel output alone is insufficient for promotion",
                "P3/P5/P8 parity blockers are outside this P6.6 lane",
                "No Pi target, provider, browser, screenshot, memory, or TUI projection behavior is claimed",
            ],
            "all_expected_passed": not job_manager_only and missing_lifecycle is None,
        }

    def resolve_manifest_path(self, manifest_path: str) -> Path:
        path = Path(manifest_path.split("#", 1)[0])
        if path.is_absolute():
            return path
        repo_path = self.repo_root / path
        if repo_path.exists():
            return repo_path
        return self.workspace / path


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def display_path(path: Path) -> str:
    if hasattr(builder, "display_path"):
        return builder.display_path(path)
    try:
        return path.resolve().relative_to(builder.ROOT.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def assert_schema_valid(schema_path: Path, record: Mapping[str, Any]) -> None:
    schema = load_json(schema_path)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(record),
        key=lambda error: (tuple(str(part) for part in error.absolute_path), error.message),
    )
    assert errors == []


def artifact_path(sandbox: BuilderSandbox, names: Sequence[str], fallbacks: Sequence[Path]) -> Path:
    candidates = [Path(getattr(builder, name)) for name in names if hasattr(builder, name)] + list(fallbacks)
    for path in candidates:
        if path.exists():
            return path
    raise AssertionError(f"expected one of these artifacts to exist: {[str(path) for path in candidates]}")


def work_items_path(sandbox: BuilderSandbox) -> Path:
    return artifact_path(
        sandbox,
        ["WORK_ITEMS_PATH", "WORK_ITEM_PATH", "WORK_ITEM_RECORDS_PATH"],
        [sandbox.work_items_path, builder.LANE_DIR / "work_items.json"],
    )


def work_item_replay_path(sandbox: BuilderSandbox) -> Path:
    return artifact_path(
        sandbox,
        ["WORK_ITEM_REPLAY_PATH", "REPLAY_PATH"],
        [sandbox.work_item_replay_path, builder.LANE_DIR / "work_item_replay.json", builder.REPLAY_PATH],
    )


def extract_work_items(sandbox: BuilderSandbox) -> list[dict[str, Any]]:
    path = work_items_path(sandbox)
    payload = json.loads(path.read_text(encoding="utf-8"))
    records: Any
    if isinstance(payload, list):
        records = payload
    elif isinstance(payload, dict) and payload.get("schema_version") == "bb.work_item.v1":
        records = [payload]
    elif isinstance(payload, dict):
        records = payload.get("work_items") or payload.get("records") or payload.get("items")
    else:
        records = None
    assert isinstance(records, list)
    assert records
    for record in records:
        assert isinstance(record, dict)
    return records


def assert_optional_schema_valid(attr_name: str, record: Mapping[str, Any]) -> None:
    schema_path = getattr(builder, attr_name, None)
    if schema_path is not None and Path(schema_path).exists():
        assert_schema_valid(Path(schema_path), record)


def assert_ref_hash_current(sandbox: BuilderSandbox, ref: str) -> None:
    assert "#sha256:" in ref
    path_text, digest = ref.rsplit("#", 1)
    path = sandbox.resolve_manifest_path(path_text)
    assert digest == sha256_file(path)


def build_valid_chain(sandbox: BuilderSandbox) -> None:
    reports = sandbox.seed_capture_inputs()
    builder.build_raw_capture_manifest(reports, GENERATED_AT)
    builder.build_work_item_replay_and_comparator(GENERATED_AT)


def build_validated_support_chain(sandbox: BuilderSandbox) -> None:
    build_valid_chain(sandbox)
    builder.build_secret_scan(GENERATED_AT)
    builder.build_prevalidation(GENERATED_AT)
    sandbox.seed_support_inputs()
    builder.update_ledger_and_support(GENERATED_AT)

def failure_text_from_artifacts(sandbox: BuilderSandbox) -> str:
    parts: list[str] = []
    for path in [work_item_replay_path(sandbox), builder.COMPARATOR_PATH, builder.PARITY_PATH]:
        if path.exists():
            parts.append(json.dumps(json.loads(path.read_text(encoding="utf-8")), sort_keys=True))
    return "\n".join(parts).lower()


def test_builder_constants_match_p6_6_contract() -> None:
    assert builder.LANE_ID == EXPECTED_LANE_ID
    assert builder.CONFIG_ID == EXPECTED_CONFIG_ID
    assert builder.CLAIM_ID == EXPECTED_CLAIM_ID


def test_work_items_are_schema_valid_deterministic_and_capture_joined_detached_lifecycles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = BuilderSandbox(tmp_path, monkeypatch)
    build_valid_chain(sandbox)

    artifact_paths = [
        work_items_path(sandbox),
        work_item_replay_path(sandbox),
        builder.COMPARATOR_PATH,
        builder.PARITY_PATH,
    ]
    first_bytes = {path: path.read_bytes() for path in artifact_paths}
    builder.build_work_item_replay_and_comparator(GENERATED_AT)
    assert {path: path.read_bytes() for path in artifact_paths} == first_bytes

    work_items = extract_work_items(sandbox)
    schema_path = Path(getattr(builder, "WORK_ITEM_SCHEMA_PATH", builder.ROOT / "contracts/kernel/schemas/bb.work_item.v1.schema.json"))
    for work_item in work_items:
        assert_schema_valid(schema_path, work_item)
        assert builder.validate_work_item(work_item) == []
        assert work_item["schema_version"] == "bb.work_item.v1"

    identities = [item["identity"] for item in work_items]
    states = [item["state"] for item in work_items]
    assert any(identity["task_kind"] == "subagent" and identity["subagent_id"] == "joined-subagent" for identity in identities)
    assert any(identity["task_kind"] == "subagent" and identity["subagent_id"] == "detached-subagent" for identity in identities)
    assert any(identity["task_kind"] == "background" and identity["correlation_id"] == "job-detached-1" for identity in identities)
    assert any(state["status"] == "completed" for state in states)
    assert any(state["status"] == "cancelled" for state in states)
    assert any(item["cancellation_policy"]["mode"] in {"cooperative", "immediate"} for item in work_items)
    assert any(item["resume_policy"]["mode"] in {"checkpoint", "replay", "manual", "signal"} for item in work_items)


def test_replay_and_comparator_pin_exact_task_job_subagent_scope_and_assertions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = BuilderSandbox(tmp_path, monkeypatch)
    build_valid_chain(sandbox)

    replay = load_json(work_item_replay_path(sandbox))
    comparator = load_json(builder.COMPARATOR_PATH)
    parity = load_json(builder.PARITY_PATH)

    assert replay["exit_status"] == "passed"
    assert all(record["schema_version"] == "bb.work_item.v1" for record in replay["normalized_records"])
    summary = replay["replay_summary"]
    assert summary["work_item_schema_valid"] is True
    assert summary["joined_subagent_target_capture_observed"] is True
    assert summary["detached_subagent_target_capture_observed"] is True
    assert summary["background_job_observed"] is True
    assert summary["cancel_observed"] is True
    assert summary["job_manager_only_evidence"] is False
    assert summary["provider_dispatch_observed"] is True
    assert summary["network_observed"] is True
    assert summary["fetch_event_count"] == 1

    assert comparator["scope"]["config_id"] == builder.CONFIG_ID
    assert comparator["scope"]["lane_id"] == builder.LANE_ID
    assert comparator["scope"]["target_family"] == builder.TARGET_FAMILY
    assert comparator["scope"]["job_manager_only_evidence"] is False
    assert comparator["passed"] is True
    assert comparator["failed"] == 0
    assertion_by_name = {assertion["name"]: assertion for assertion in comparator["assertions"]}
    assert REQUIRED_COMPARATOR_ASSERTIONS <= set(assertion_by_name)
    for name in REQUIRED_COMPARATOR_ASSERTIONS:
        assert assertion_by_name[name]["status"] == "passed"
    assert assertion_by_name["target_joined_subagent_lifecycle_observed"]["observed"]["capture_count"] >= 1
    assert assertion_by_name["target_detached_subagent_lifecycle_observed"]["observed"]["capture_count"] >= 1
    assert assertion_by_name["background_job_lifecycle_observed"]["observed"]["background_job_count"] >= 1
    assert assertion_by_name["cancel_lifecycle_observed"]["observed"]["cancelled_work_item_count"] >= 1
    assert assertion_by_name["job_manager_only_evidence_rejected"]["observed"]["job_manager_only_evidence"] is False

    assert parity["status"] == "passed"
    assert parity["comparator_ref"] == f"{display_path(builder.COMPARATOR_PATH)}#{sha256_file(builder.COMPARATOR_PATH)}"
    for manifest_path, digest in comparator["input_hashes"].items():
        assert digest == sha256_file(sandbox.resolve_manifest_path(manifest_path))


def test_job_manager_only_list_poll_cancel_evidence_cannot_promote_p6_6(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = BuilderSandbox(tmp_path, monkeypatch)
    reports = sandbox.seed_capture_inputs(job_manager_only=True)
    builder.build_raw_capture_manifest(reports, GENERATED_AT)

    try:
        builder.build_work_item_replay_and_comparator(GENERATED_AT)
    except (AssertionError, RuntimeError, ValueError):
        return

    replay = load_json(work_item_replay_path(sandbox))
    comparator = load_json(builder.COMPARATOR_PATH)
    summary = replay.get("replay_summary", {})
    assertions = {assertion["name"]: assertion for assertion in comparator.get("assertions", [])}
    assert summary.get("job_manager_only_evidence") is True
    assert (
        replay.get("exit_status") == "failed"
        or comparator.get("passed") is False
        or assertions.get("job_manager_only_evidence_rejected", {}).get("status") == "failed"
    )
    assert not (replay.get("exit_status") == "passed" and comparator.get("passed") is True)


@pytest.mark.parametrize(
    ("missing_lifecycle", "expected_error_name"),
    [
        ("joined", "remaining_prerequisites_missing_joined_subagent_lifecycle_evidence"),
        ("detached", "remaining_prerequisites_missing_detached_subagent_lifecycle_evidence"),
    ],
)
def test_job_list_poll_cancel_with_missing_joined_or_detached_lifecycle_pins_remaining_prerequisite_blocker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    missing_lifecycle: str,
    expected_error_name: str,
) -> None:
    sandbox = BuilderSandbox(tmp_path, monkeypatch)
    reports = sandbox.seed_capture_inputs(missing_lifecycle=missing_lifecycle)
    builder.build_raw_capture_manifest(reports, GENERATED_AT)

    try:
        builder.build_work_item_replay_and_comparator(GENERATED_AT)
    except (AssertionError, RuntimeError, ValueError) as exc:
        assert expected_error_name in str(exc).lower()
        return

    text = failure_text_from_artifacts(sandbox)
    replay = load_json(work_item_replay_path(sandbox))
    comparator = load_json(builder.COMPARATOR_PATH)
    assert expected_error_name in text
    assert replay.get("exit_status") == "failed" or comparator.get("passed") is False
    assert not (replay.get("exit_status") == "passed" and comparator.get("passed") is True)


def test_support_claim_boundaries_evidence_manifest_roles_and_current_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = BuilderSandbox(tmp_path, monkeypatch)
    build_validated_support_chain(sandbox)

    support = load_json(builder.SUPPORT_CLAIM_PATH)
    evidence_manifest = load_json(builder.EVIDENCE_MANIFEST_PATH)
    comparator_scope = load_json(builder.COMPARATOR_PATH)["scope"]

    assert_optional_schema_valid("SUPPORT_CLAIM_SCHEMA_PATH", support)
    assert_optional_schema_valid("EVIDENCE_MANIFEST_SCHEMA_PATH", evidence_manifest)

    assert support["schema_version"] == "bb.e4.support_claim.v1"
    assert support["accepted"] is True
    assert support["points"] == 20
    assert support["claim_id"] == builder.CLAIM_ID
    assert support["config_id"] == builder.CONFIG_ID
    assert support["lane_id"] == builder.LANE_ID
    assert support["tool_id"] == builder.LANE_ID
    assert support["scope"]["config_id"] == builder.CONFIG_ID
    assert support["scope"]["lane_id"] == builder.LANE_ID
    assert support["scope"]["target_family"] == builder.TARGET_FAMILY
    assert support["scope"]["job_manager_only_evidence"] is False
    assert support["scope"]["joined_subagent_target_capture"] is True
    assert support["scope"]["detached_subagent_target_capture"] is True
    assert support["scope"]["background_job_observed"] is True
    assert support["scope"]["cancel_observed"] is True
    assert support["scope"]["provider_authenticated_capture"] is True
    assert support["scope"]["provider_dispatch_observed"] is True
    assert support["scope"]["network_observed"] is True
    assert support["scope"]["provider_parity_claimed"] is False
    assert all(comparator_scope.get(key) == value for key, value in support["scope"].items())

    exclusions = "\n".join(support["exclusions"]).lower()
    for keyword in [
        "job-manager-only",
        "job manager only",
        "p3",
        "p5",
        "p8",
        "pi target",
        "provider",
        "browser",
        "screenshot",
        "memory",
        "tui",
    ]:
        assert keyword in exclusions

    assert_ref_hash_current(sandbox, support["capture_ref"])
    assert_ref_hash_current(sandbox, support["replay_ref"])
    assert_ref_hash_current(sandbox, support["comparator_ref"])
    assert_ref_hash_current(sandbox, support["secret_scan_ref"])
    for ref in support["validation_refs"]:
        assert_ref_hash_current(sandbox, ref)

    assert evidence_manifest["schema_version"] == "bb.e4.evidence_manifest.v1"
    assert evidence_manifest["claim_id"] == builder.CLAIM_ID
    assert evidence_manifest["config_id"] == builder.CONFIG_ID
    assert evidence_manifest["lane_id"] == builder.LANE_ID
    artifacts = evidence_manifest["artifacts"]
    roles = {artifact["role"] for artifact in artifacts}
    assert REQUIRED_EVIDENCE_ROLES <= roles

    artifact_by_role = {artifact["role"]: artifact for artifact in artifacts}
    assert artifact_by_role["freeze_manifest"]["sha256"] == builder.freeze_row_hash()
    for role, artifact in artifact_by_role.items():
        if role == "freeze_manifest":
            continue
        assert artifact["sha256"] == sha256_file(sandbox.resolve_manifest_path(artifact["path"]))

    assert artifact_by_role["joined_subagent_target_capture"]["path"] == display_path(builder.JOINED_CAPTURE_PATH)
    assert artifact_by_role["detached_subagent_target_capture"]["path"] == display_path(builder.DETACHED_CAPTURE_PATH)
    assert artifact_by_role["work_item_replay"]["path"] == display_path(work_item_replay_path(sandbox))
    assert artifact_by_role["task_job_subagent_comparator"]["path"] == display_path(builder.COMPARATOR_PATH)

    joined_ref = f"{display_path(builder.JOINED_CAPTURE_PATH)}#{sha256_file(builder.JOINED_CAPTURE_PATH)}"
    detached_ref = f"{display_path(builder.DETACHED_CAPTURE_PATH)}#{sha256_file(builder.DETACHED_CAPTURE_PATH)}"
    work_item_ref = f"{display_path(work_items_path(sandbox))}#{sha256_file(work_items_path(sandbox))}"
    assert joined_ref in artifact_by_role["work_item_replay"]["derived_from"]
    assert detached_ref in artifact_by_role["work_item_replay"]["derived_from"]
    assert work_item_ref in artifact_by_role["replay_ref"]["derived_from"]
    assert support["replay_ref"] in artifact_by_role["task_job_subagent_comparator"]["derived_from"]
