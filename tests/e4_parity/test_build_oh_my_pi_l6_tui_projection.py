from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

import pytest
from jsonschema import Draft202012Validator

from scripts.e4_parity import build_oh_my_pi_l6_tui_projection as builder


GENERATED_AT = "2026-07-02T00:00:00Z"
SCOPE_EXTRAS = {
    "terminal_width": 100,
    "plain": True,
    "projection_boundary": "host-visible projection-only; kernel_truth=false",
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
            "AGENT_CONFIG_PATH": self.repo_root / "agent_configs" / "misc" / "oh_my_pi_p6_0_l6_tui_projection_v1.yaml",
            "LANE_DIR": self.lane_dir,
            "RAW_DIR": self.raw_dir,
            "TARGET_HOME": self.lane_dir / "target_home",
            "PROBE_SCRIPT_PATH": self.lane_dir / f"{builder.LANE_ID}_probe.mjs",
            "PROJECTION_EVENTS_PATH": self.lane_dir / "projection_events.json",
            "RAW_CAPTURE_PATH": self.lane_dir / "raw_capture_manifest.json",
            "TARGET_PROBE_PATH": self.lane_dir / "target_probe_output.json",
            "SETUP_REPORT_PATH": self.lane_dir / "target_setup_and_capture_report.json",
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
        for relative_path in [
            "packages/coding-agent/src/cli/gallery-cli.ts",
            "packages/coding-agent/src/commands/gallery.ts",
            "packages/coding-agent/src/modes/components/tool-execution.ts",
            "packages/coding-agent/src/modes/components/chat-transcript-builder.ts",
            "packages/tui/src/tui.ts",
            "packages/coding-agent/src/tools/renderers.ts",
            "packages/coding-agent/src/cli/gallery-fixtures/index.ts",
        ]:
            self.write_text(self.source_root / relative_path, f"// {relative_path}\n")

    def seed_capture_inputs(self) -> list[dict[str, Any]]:
        self.seed_source_files()
        self.write_json(
            builder.TARGET_PROBE_PATH,
            {
                "provider_dispatch_observed": False,
                "network_observed": False,
                "fetch_events": [],
            },
        )
        self.write_json(builder.SETUP_REPORT_PATH, {"schema_version": "test.setup"})
        self.write_text(builder.PROBE_SCRIPT_PATH, "console.log('probe')\n")
        self.write_text(builder.AGENT_CONFIG_PATH, "provider: no-provider\n")
        self.write_text(builder.LANE_DIR / "target_fixture_workspace" / ".omp" / "SYSTEM.md", "system fixture\n")

        reports: list[dict[str, Any]] = []
        stdout_by_name = {
            "omp_gallery_read_success_plain": "── read — Read\n· done\nOutput\nread body\n",
            "omp_gallery_bash_error_plain": "── bash — Bash\n· failed\nExit: 2\nbash body\n",
            "omp_gallery_report_tool_issue_success_plain": "── report_tool_issue — Report Tool Issue\n· done\nNoted, thanks!\n",
        }
        for spec in builder.COMMAND_SPECS:
            name = spec["name"]
            stdout_path = builder.RAW_DIR / f"{name}.stdout.txt"
            stderr_path = builder.RAW_DIR / f"{name}.stderr.txt"
            report_path = builder.RAW_DIR / f"{name}.command_report.json"
            self.write_text(stdout_path, stdout_by_name.get(name, "probe stdout\n"))
            self.write_text(stderr_path, "")
            report = {
                "name": name,
                "exit_code": 0,
                "report_path": builder.display_path(report_path),
                "stdout_sha256": sha256_file(stdout_path),
                "stderr_sha256": sha256_file(stderr_path),
            }
            self.write_json(report_path, report)
            reports.append(report)
        return reports

    def seed_support_inputs(self) -> None:
        self.write_json(self.source_freeze, {"commit": builder.UPSTREAM_COMMIT})
        self.write_json(self.ledger_path, {"rows": []})
        self.write_text(
            builder.FREEZE_MANIFEST_PATH,
            "e4_configs:\n"
            f"  {builder.CONFIG_ID}:\n"
            f"    id: {builder.CONFIG_ID}\n"
            "    lane_id: oh_my_pi_p6_0_l6_tui_projection\n"
            "    target_family: oh_my_pi\n",
        )
        self.write_json(builder.SECRET_SCAN_PATH, {"findings": []})
        self.write_json(builder.PREVALIDATION_PATH, {"ok": True, "errors": []})


def sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def assert_projection_event_schema_valid(record: Mapping[str, Any]) -> None:
    schema = load_json(builder.PROJECTION_SCHEMA_PATH)
    errors = sorted(
        Draft202012Validator(schema).iter_errors(record),
        key=lambda error: (tuple(str(part) for part in error.absolute_path), error.message),
    )
    assert errors == []


def test_projection_events_are_schema_valid_projection_only_host_surfaces(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = BuilderSandbox(tmp_path, monkeypatch)
    sandbox.seed_capture_inputs()

    records = builder.build_projection_events(GENERATED_AT)

    assert {record["projection_event_id"] for record in records} == {
        f"{builder.LANE_ID}_read_success",
        f"{builder.LANE_ID}_bash_error",
        f"{builder.LANE_ID}_report_tool_issue_success",
    }
    for record in records:
        assert_projection_event_schema_valid(record)
        assert builder.validate_projection_event(record) == []
        assert record["schema_version"] == "bb.projection_event.v1"
        assert record["kernel_truth"] is False
        assert record["visibility"] == {
            "model_visible": False,
            "host_visible": True,
            "provider_visible": False,
        }
        assert record["projection_surface"]["audience"] == "host"
        assert record["projection_surface"]["path"] == "stdout"
        assert record["projection_payload_ref"]["media_type"] == "text/plain; charset=utf-8"
        assert record["projection_payload_ref"]["redaction_state"] == "none"
        assert [frame["visible_to_model"] for frame in record["status_frames"]] == [False]
        assert [frame["visible_to_host"] for frame in record["status_frames"]] == [True]

    manifest = load_json(builder.PROJECTION_EVENTS_PATH)
    assert manifest["schema_version"] == "bb.e4.projection_events_manifest.v1"
    assert manifest["lane_id"] == builder.LANE_ID
    assert manifest["records"] == records


def test_raw_capture_manifest_sources_exclude_kernel_contract_schema_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = BuilderSandbox(tmp_path, monkeypatch)
    reports = sandbox.seed_capture_inputs()

    builder.build_raw_capture_manifest(reports, GENERATED_AT)

    manifest = load_json(builder.RAW_CAPTURE_PATH)
    source_hashes = manifest["source_hashes"]
    assert isinstance(source_hashes, dict)
    assert any(path.endswith("packages/coding-agent/src/cli/gallery-cli.ts") for path in source_hashes)
    assert all(isinstance(digest, str) and digest.startswith("sha256:") for digest in source_hashes.values())
    assert all("contracts/kernel" not in path for path in source_hashes)
    assert all(
        "contracts/kernel" not in artifact["path"]
        for artifact in manifest["source_artifacts"]
    )
    assert manifest["projection_scope"] == {
        "terminal_width": 100,
        "plain": True,
        "selected_gallery_tools": ["read", "bash", "report_tool_issue"],
        "selected_lifecycle_states": ["success", "error"],
        "projection_primitive": "bb.projection_event.v1",
        "kernel_truth": False,
        "model_visible": False,
        "host_visible": True,
        "provider_visible": False,
    }


def test_support_claim_scope_does_not_outgrow_comparator_projection_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sandbox = BuilderSandbox(tmp_path, monkeypatch)
    reports = sandbox.seed_capture_inputs()
    sandbox.seed_support_inputs()
    builder.build_raw_capture_manifest(reports, GENERATED_AT)
    builder.build_replay_and_comparator(GENERATED_AT)

    builder.update_ledger_and_support(GENERATED_AT)

    comparator_scope = load_json(builder.COMPARATOR_PATH)["scope"]
    support_scope = load_json(builder.SUPPORT_CLAIM_PATH)["scope"]
    assert {key: support_scope[key] for key in SCOPE_EXTRAS} == SCOPE_EXTRAS
    assert {key: comparator_scope[key] for key in SCOPE_EXTRAS} == SCOPE_EXTRAS
    assert all(comparator_scope.get(key) == value for key, value in support_scope.items())
