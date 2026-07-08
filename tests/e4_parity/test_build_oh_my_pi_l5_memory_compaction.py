from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import pytest
from jsonschema import Draft202012Validator

from scripts.e4_parity import build_oh_my_pi_l5_memory_compaction as builder


GENERATED_AT = "2026-07-03T00:00:00Z"
EXPECTED_SUPPORT_SCOPE = {
    "config_id": builder.CONFIG_ID,
    "lane_id": builder.LANE_ID,
    "run_id": builder.RUN_ID,
    "target_version": builder.TARGET_VERSION,
    "provider_model": builder.PROVIDER_MODEL,
    "sandbox_mode": builder.SANDBOX_MODE,
    "memory_boundary": "provider-free snapcompact compaction plus memory-backend/tool visibility only",
    "compaction_strategy": "snapcompact",
    "remote_compaction": False,
    "memory_backend_state": "none absent; mnemopi/hindsight blocked without initialized state",
}
EXPECTED_COMPARATOR_SCOPE = {"target_family": builder.TARGET_FAMILY, **EXPECTED_SUPPORT_SCOPE}
REQUIRED_EVIDENCE_ROLES = {
    "freeze_manifest",
    "capture_ref",
    "replay_ref",
    "comparator_ref",
    "parity_results",
    "secret_scan_report",
    "validator_output",
    "support_claim_ref",
    "atomic_feature_ledger",
    "memory_compaction_plan",
    "transcript_continuation_patch",
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

        replacements = {
            "ROOT": self.repo_root,
            "WORKSPACE": self.workspace,
            "SOURCE_ROOT": self.source_root,
            "SOURCE_FREEZE": self.source_freeze,
            "LEDGER_PATH": self.ledger_path,
            "FREEZE_MANIFEST_PATH": self.repo_root / "config" / "e4_target_freeze_manifest.yaml",
            "AGENT_CONFIG_PATH": self.repo_root / "agent_configs" / "misc" / "oh_my_pi_p6_0_l5_memory_compaction_v1.yaml",
            "LANE_DIR": self.lane_dir,
            "RAW_DIR": self.raw_dir,
            "TARGET_HOME": self.lane_dir / "target_home",
            "PROBE_SCRIPT_PATH": self.lane_dir / f"{builder.LANE_ID}_probe.mjs",
            "RAW_CAPTURE_PATH": self.lane_dir / "raw_capture_manifest.json",
            "TARGET_PROBE_PATH": self.lane_dir / "target_probe_output.json",
            "SETUP_REPORT_PATH": self.lane_dir / "target_setup_and_capture_report.json",
            "TRANSCRIPT_FIXTURE_PATH": self.lane_dir / "target_transcript_fixture.json",
            "MEMORY_PLAN_PATH": self.lane_dir / "memory_compaction_plan.v1.json",
            "TRANSCRIPT_PATCH_PATH": self.lane_dir / "transcript_continuation_patch.v1.json",
            "REPLAY_PATH": self.lane_dir / "bb_replay_result.json",
            "COMPARATOR_PATH": self.lane_dir / "comparator_report.json",
            "PARITY_PATH": self.lane_dir / "parity_results.json",
            "SECRET_SCAN_PATH": self.lane_dir / "secret_scan_report.json",
            "PREVALIDATION_PATH": self.lane_dir / "prevalidation_report.json",
            "SUPPORT_CLAIM_PATH": self.repo_root / "docs" / "conformance" / "support_claims" / f"{builder.CLAIM_ID}.json",
            "EVIDENCE_MANIFEST_PATH": self.repo_root / "docs" / "conformance" / "support_claims" / f"{builder.CLAIM_ID.replace('_support_claim', '_evidence_manifest')}.json",
        }
        for name, value in replacements.items():
            monkeypatch.setattr(builder, name, value)

    def write_text(self, path: Path, content: str = "fixture\n") -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def write_json(self, path: Path, payload: Mapping[str, Any] | list[Any]) -> None:
        self.write_text(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")

    def seed_source_files(self) -> None:
        for relative_path in builder.SOURCE_REF_PATHS:
            self.write_text(self.source_root / relative_path, f"// {relative_path}\n")

    def seed_capture_inputs(self) -> list[dict[str, Any]]:
        self.seed_source_files()
        self.write_json(builder.TARGET_PROBE_PATH, self.target_probe_payload())
        self.write_json(builder.SETUP_REPORT_PATH, {"schema_version": "test.setup", "ok": True})
        self.write_text(builder.PROBE_SCRIPT_PATH, "console.log('probe')\n")
        self.write_text(builder.AGENT_CONFIG_PATH, "provider: no-provider\nsandbox_mode: read-only\n")
        self.write_text(builder.LANE_DIR / "target_fixture_workspace" / ".omp" / "SYSTEM.md", "system fixture\n")

        reports: list[dict[str, Any]] = []
        for spec in builder.COMMAND_SPECS:
            name = str(spec["name"])
            stdout_path = builder.RAW_DIR / f"{name}.stdout.txt"
            stderr_path = builder.RAW_DIR / f"{name}.stderr.txt"
            report_path = builder.RAW_DIR / f"{name}.command_report.json"
            stdout = "16.2.13\n" if name == "omp_version" else json.dumps(self.target_probe_payload(), sort_keys=True) + "\n"
            self.write_text(stdout_path, stdout)
            self.write_text(stderr_path, "")
            report = {
                "schema_version": "bb.e4.command_report.v1",
                "name": name,
                "argv": list(spec["argv"]),
                "cwd": str(self.source_root),
                "exit_code": 0,
                "report_path": builder.display_path(report_path),
                "stdout_path": builder.display_path(stdout_path),
                "stderr_path": builder.display_path(stderr_path),
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

    def target_probe_payload(self) -> dict[str, Any]:
        observations = [
            {
                "tool": tool,
                "backend": "none",
                "status": "absent",
                "created": False,
                "approval": None,
                "loadMode": None,
                "strict": None,
            }
            for tool in ["retain", "recall", "reflect"]
        ]
        observations.extend(
            {
                "tool": tool,
                "backend": backend,
                "status": "blocked",
                "created": True,
                "approval": "read",
                "loadMode": "discoverable",
                "strict": True,
                "error": f"{backend} backend is not initialised for this session.",
            }
            for backend in ["mnemopi", "hindsight"]
            for tool in ["retain", "recall", "reflect"]
        )
        return {
            "schema_version": "bb.e4.oh_my_pi_p6_0_l5_memory_compaction_report.v1",
            "target_family": builder.TARGET_FAMILY,
            "target_version": builder.TARGET_VERSION,
            "config_id": builder.CONFIG_ID,
            "lane_id": builder.LANE_ID,
            "run_id": builder.RUN_ID,
            "provider_model": builder.PROVIDER_MODEL,
            "sandbox_mode": builder.SANDBOX_MODE,
            "provider_dispatch_observed": False,
            "network_observed": False,
            "fetch_events": [],
            "errors": [],
            "warnings": [],
            "all_expected_passed": True,
            "transcript_fixture": {
                "transcript_id": "oh_my_pi_p6_0_l5_transcript_fixture",
                "entries": [
                    {"id": "entry-000-user", "type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "inspect src/alpha.ts"}]}},
                    {"id": "entry-001-assistant-tool-call", "type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "reading"}]}},
                    {"id": "entry-002-tool-result", "type": "message", "message": {"role": "toolResult", "content": [{"type": "text", "text": "alpha true"}]}},
                    {"id": "entry-003-user-recent", "type": "message", "message": {"role": "user", "content": [{"type": "text", "text": "keep current handoff"}]}},
                    {"id": "entry-004-assistant-recent", "type": "message", "message": {"role": "assistant", "content": [{"type": "text", "text": "latest verification remains live"}]}},
                ],
            },
            "compaction_runtime": {
                "llmContext": {"role": "user", "attribution": "agent", "providerPayloadPresent": False},
                "preparation": {
                    "messagesToSummarizeCount": 3,
                    "recentMessagesCount": 1,
                    "firstKeptEntryId": "entry-004-assistant-recent",
                    "tokensBefore": 128960,
                    "turnPrefixMessagesCount": 1,
                    "isSplitTurn": True,
                    "fileOps": {"read": ["src/alpha.ts"], "edited": [], "written": []},
                },
                "settings": {"reserveTokens": 16384, "strategy": "snapcompact", "remoteEnabled": False},
                "thresholds": {"defaultThreshold": 108800, "fixedThreshold": 64000, "percentThreshold": 96000},
                "snapcompactResult": {
                    "summary": "Archived deterministic prior conversation into HISTORY and continue from the live suffix.",
                    "shortSummary": "Archived 128 chars of history",
                    "firstKeptEntryId": "entry-004-assistant-recent",
                    "tokensBefore": 128960,
                    "archive": {
                        "hasArchive": True,
                        "frameCount": 0,
                        "historyBlockTypes": ["text"],
                        "imageBlockCount": 0,
                        "textChars": 128,
                        "textHeadChars": 128,
                        "textTailChars": 0,
                        "totalChars": 128,
                        "truncatedChars": 0,
                    },
                },
            },
            "memory_backend_visibility": {
                "noneBackendToolsAbsent": True,
                "configuredBackendsBlockedWithoutInitializedState": True,
                "observations": observations,
            },
        }

    def resolve_manifest_path(self, manifest_path: str) -> Path:
        path = Path(manifest_path)
        if path.is_absolute():
            return path
        repo_path = self.repo_root / path
        if repo_path.exists():
            return repo_path
        return self.workspace / path


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


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


def build_replay_chain(sandbox: BuilderSandbox) -> None:
    reports = sandbox.seed_capture_inputs()
    builder.build_raw_capture_manifest(reports, GENERATED_AT)
    builder.build_replay_and_comparator(GENERATED_AT)
    builder.build_raw_capture_manifest(reports, GENERATED_AT)
    builder.build_replay_and_comparator(GENERATED_AT)


def build_validated_support_chain(sandbox: BuilderSandbox) -> None:
    build_replay_chain(sandbox)
    builder.build_secret_scan(GENERATED_AT)
    builder.build_prevalidation(GENERATED_AT)
    sandbox.seed_support_inputs()
    builder.update_ledger_and_support(GENERATED_AT)


def test_memory_plan_and_patch_are_schema_valid_deterministic_transcript_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = BuilderSandbox(tmp_path, monkeypatch)
    sandbox.seed_capture_inputs()

    plan, patch = builder.build_memory_plan_and_patch(GENERATED_AT)
    first_plan_bytes = builder.MEMORY_PLAN_PATH.read_bytes()
    first_patch_bytes = builder.TRANSCRIPT_PATCH_PATH.read_bytes()
    second_plan, second_patch = builder.build_memory_plan_and_patch(GENERATED_AT)

    assert plan == second_plan
    assert patch == second_patch
    assert builder.MEMORY_PLAN_PATH.read_bytes() == first_plan_bytes
    assert builder.TRANSCRIPT_PATCH_PATH.read_bytes() == first_patch_bytes
    assert_schema_valid(builder.MEMORY_SCHEMA_PATH, plan)
    assert_schema_valid(builder.PATCH_SCHEMA_PATH, patch)
    assert plan["schema_version"] == "bb.memory_compaction_plan.v1"
    assert patch["schema_version"] == "bb.transcript_continuation_patch.v1"
    assert plan["plan_id"] == f"{builder.LANE_ID}_snapcompact_plan"
    assert patch["patch_id"] == f"{builder.LANE_ID}_snapcompact_patch"
    assert plan["status"] == "applied"
    assert plan["hashes"]["plan_hash"] == builder._plan_hash(plan)

    appended_message = patch["appended_messages"][0]
    assert appended_message == {
        "role": "compactionSummary",
        "summary": "Archived deterministic prior conversation into HISTORY and continue from the live suffix.",
        "shortSummary": "Archived 128 chars of history",
        "firstKeptEntryId": "entry-004-assistant-recent",
        "tokensBefore": 128960,
        "modelVisible": True,
        "providerVisible": True,
    }
    assert patch["compaction_markers"] == [
        {
            "strategy": "snapcompact",
            "firstKeptEntryId": "entry-004-assistant-recent",
            "messagesToSummarizeCount": 3,
            "recentMessagesCount": 1,
            "tokensBefore": 128960,
            "archiveFrameCount": 0,
            "archiveTextHeadChars": 128,
            "memoryBackendVisibility": "none absent; mnemopi/hindsight tools blocked without initialized state",
        }
    ]
    assert patch["lossiness_flags"] == [
        "older_history_elided_to_snapcompact_archive",
        "memory_backend_configured_tool_visibility_captured_without_backend_state",
    ]

    patch_hash = sha256_file(builder.TRANSCRIPT_PATCH_PATH)
    assert plan["trigger"]["kind"] == "manual"
    assert plan["trigger"]["observed_tokens"] == 128960
    assert plan["trigger"]["threshold_tokens"] == 108800
    assert plan["elided_refs"][0]["replacement_ref"].endswith(f"#{patch_hash}")
    assert plan["model_visible_insertions"] == [
        {
            "insertion_id": "compaction_summary_message",
            "position": "between_turns",
            "content_ref": builder.display_path(builder.TRANSCRIPT_PATCH_PATH),
            "content_hash": patch_hash,
            "token_estimate": 32,
        }
    ]
    assert [contribution["contribution_id"] for contribution in plan["backend_contributions"]] == [
        "snapcompact_local_archive",
        "memory_backend_tool_visibility",
    ]
    assert plan["backend_contributions"][1]["visibility"] == {
        "model_visible": False,
        "provider_visible": False,
        "host_visible": True,
    }


def test_replay_and_comparator_pin_exact_memory_scope_and_assertions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = BuilderSandbox(tmp_path, monkeypatch)
    build_replay_chain(sandbox)

    replay = load_json(builder.REPLAY_PATH)
    comparator = load_json(builder.COMPARATOR_PATH)
    parity = load_json(builder.PARITY_PATH)

    assert replay["exit_status"] == "passed"
    assert [record["schema_version"] for record in replay["normalized_records"]] == [
        "bb.memory_compaction_plan.v1",
        "bb.transcript_continuation_patch.v1",
    ]
    assert replay["replay_summary"] == {
        "command_count": 2,
        "memory_compaction_plan_schema_valid": True,
        "transcript_patch_schema_valid": True,
        "messages_to_summarize_count": 3,
        "snapcompact_archive_present": True,
        "memory_backend_none_tools_absent": True,
        "memory_backends_blocked_without_initialized_state": True,
        "provider_dispatch_observed": False,
        "network_observed": False,
        "fetch_event_count": 0,
    }

    assert comparator["scope"] == EXPECTED_COMPARATOR_SCOPE
    assert comparator["passed"] is True
    assert comparator["failed"] == 0
    assert comparator["warned"] == 0
    assert {assertion["name"] for assertion in comparator["assertions"]} == {
        "scope_matches_exact_claim",
        "commands_exit_zero",
        "target_compaction_runtime_exercised",
        "breadboard_memory_plan_and_patch_valid",
        "memory_backend_visibility_captured",
        "provider_network_scope_limited",
    }
    assert all(assertion["status"] == "passed" for assertion in comparator["assertions"])
    assertion_by_name = {assertion["name"]: assertion for assertion in comparator["assertions"]}
    assert assertion_by_name["breadboard_memory_plan_and_patch_valid"]["observed"] == {
        "plan_schema_errors": 0,
        "patch_schema_errors": 0,
        "elided_refs": 1,
        "model_visible_insertions": 1,
        "backend_contributions": 2,
    }
    assert assertion_by_name["memory_backend_visibility_captured"]["observed"] == {
        "none_backend_tools_absent": True,
        "configured_backends_blocked_without_initialized_state": True,
        "observation_count": 9,
    }
    assert assertion_by_name["provider_network_scope_limited"]["observed"] == {
        "fetch_event_count": 0,
        "network_observed": False,
        "provider_dispatch_observed": False,
    }
    assert parity["status"] == "passed"
    assert parity["comparator_ref"] == f"{builder.display_path(builder.COMPARATOR_PATH)}#{sha256_file(builder.COMPARATOR_PATH)}"

    for manifest_path, digest in comparator["input_hashes"].items():
        assert digest == sha256_file(sandbox.resolve_manifest_path(manifest_path))


def test_secret_scan_and_prevalidation_outputs_are_present_clean_and_hash_current_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = BuilderSandbox(tmp_path, monkeypatch)
    build_replay_chain(sandbox)

    builder.build_secret_scan(GENERATED_AT)
    builder.build_prevalidation(GENERATED_AT)

    secret = load_json(builder.SECRET_SCAN_PATH)
    prevalidation = load_json(builder.PREVALIDATION_PATH)

    assert secret["schema_version"] == "bb.e4.secret_scan_report.v1"
    assert secret["status"] == "passed"
    assert secret["passed"] is True
    assert secret["findings"] == []
    scanned = set(secret["scanned_artifacts"])
    assert {builder.display_path(path) for path in [builder.MEMORY_PLAN_PATH, builder.TRANSCRIPT_PATCH_PATH, builder.REPLAY_PATH, builder.COMPARATOR_PATH, builder.PARITY_PATH]} <= scanned

    assert prevalidation["schema_version"] == "bb.e4.c4_prevalidation_report.v1"
    assert prevalidation["ok"] is True
    assert prevalidation["accepted"] is True
    assert prevalidation["exit_status"] == "passed"
    assert prevalidation["errors"] == []
    assert {check["name"]: check["passed"] for check in prevalidation["checks"]} == {
        "raw_capture_present": True,
        "replay_passed": True,
        "comparator_assertions_passed": True,
        "secret_scan_passed": True,
        "memory_plan_schema_valid": True,
        "transcript_patch_schema_valid": True,
        "target_runtime_compaction_observed": True,
        "memory_backend_visibility_observed": True,
        "provider_free_network_free": True,
    }
    for manifest_path, digest in prevalidation["artifact_hashes"].items():
        assert digest == sha256_file(sandbox.resolve_manifest_path(manifest_path))


def test_support_claim_boundaries_and_evidence_manifest_roles_use_current_hashes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = BuilderSandbox(tmp_path, monkeypatch)
    build_validated_support_chain(sandbox)

    support = load_json(builder.SUPPORT_CLAIM_PATH)
    evidence_manifest = load_json(builder.EVIDENCE_MANIFEST_PATH)
    comparator_scope = load_json(builder.COMPARATOR_PATH)["scope"]

    assert support["schema_version"] == "bb.e4.support_claim.v1"
    assert support["accepted"] is True
    assert support["points"] == 15
    assert support["claim_id"] == builder.CLAIM_ID
    assert support["config_id"] == builder.CONFIG_ID
    assert support["lane_id"] == builder.LANE_ID
    assert support["tool_id"] == builder.LANE_ID
    assert support["scope"] == EXPECTED_SUPPORT_SCOPE
    assert all(comparator_scope.get(key) == value for key, value in support["scope"].items())
    assert "accepted only for the named P6.0-L5 lane" in support["summary"]

    exclusions = "\n".join(support["exclusions"]).lower()
    assert "broad oh-my-pi" in exclusions
    assert "omp support" in exclusions
    assert "no pi" in exclusions
    assert "provider" in exclusions
    assert "browser automation" in exclusions
    assert "screenshot" in exclusions
    assert "task" in exclusions
    assert "job" in exclusions
    assert "subagent" in exclusions
    assert "no initialized durable" in exclusions
    assert "remote compaction" in exclusions

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

    memory_plan_ref = f"{builder.display_path(builder.MEMORY_PLAN_PATH)}#{sha256_file(builder.MEMORY_PLAN_PATH)}"
    transcript_patch_ref = f"{builder.display_path(builder.TRANSCRIPT_PATCH_PATH)}#{sha256_file(builder.TRANSCRIPT_PATCH_PATH)}"

    assert artifact_by_role["replay_ref"]["derived_from"] == [
        support["capture_ref"],
        memory_plan_ref,
        transcript_patch_ref,
    ]
    assert artifact_by_role["comparator_ref"]["derived_from"] == [
        support["capture_ref"],
        support["replay_ref"],
        memory_plan_ref,
        transcript_patch_ref,
    ]
    assert support["secret_scan_ref"] == f"{builder.display_path(builder.SECRET_SCAN_PATH)}#{sha256_file(builder.SECRET_SCAN_PATH)}"
    assert support["validation_refs"] == [f"{builder.display_path(builder.PREVALIDATION_PATH)}#{sha256_file(builder.PREVALIDATION_PATH)}"]
