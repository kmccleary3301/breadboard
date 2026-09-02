from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from breadboard_engine.compilation.contracts import canonical_json_bytes
from breadboard.rl.phase5 import f4_campaign as f4
from breadboard.rl.phase5.f4_campaign import (
    CLAIM_BOUNDARY,
    F4CampaignInput,
    F4CampaignManifest,
    F4CampaignValidationError,
    F4ValidationReport,
    author_f4_campaign,
    validate_f4_campaign,
)


def _d(label: str) -> str:
    return "sha256:" + hashlib.sha256(label.encode()).hexdigest()


def _ref(label: str, *, scheme: str = "evidence") -> dict[str, str]:
    digest = _d(label)
    return {"reference": f"{scheme}://{label}@{digest}", "digest": digest}


def _content_ref(
    label: str, value: dict[str, object], *, scheme: str = "evidence"
) -> dict[str, str]:
    digest = "sha256:" + hashlib.sha256(canonical_json_bytes(value)).hexdigest()
    return {"reference": f"{scheme}://{label}@{digest}", "digest": digest}


def _valid_payload() -> dict[str, object]:
    identity = {
        "task_id": "R-SWE-001",
        "task_row_ref": _ref("task-row"),
        "task_contract_digest": _d("task-contract"),
        "repository_snapshot_ref": _ref("repository"),
        "model_ref": _ref("model"),
        "checkpoint_ref": _ref("checkpoint"),
        "task_image_ref": _ref("task-image"),
        "verifier_image_ref": _ref("verifier-image"),
        "verifier_ref": _ref("verifier"),
    }
    compiler = _ref("compiler")
    target_report_ref = _ref("target-report")
    environments = (
        {
            "environment_id": "local-docker",
            "environment_kind": "local-docker",
            "environment_ref": _ref("environment-local"),
            "infrastructure_path_ref": _ref("infrastructure-local"),
        },
        {
            "environment_id": "ibm-one-node",
            "environment_kind": "ibm-one-node",
            "environment_ref": _ref("environment-ibm", scheme="ibm"),
            "infrastructure_path_ref": _ref("infrastructure-ibm", scheme="ibm"),
        },
    )
    names = {
        "codex-like": "Codex-like",
        "claude-like": "Claude-like",
        "pi-like": "Pi-like",
        "opencode": "OpenCode",
        "oh-my-opencode": "oh-my-opencode",
        "unknown-name": "generated-zeta-4f19",
    }
    pointers = {
        "codex-like": "/prompts/system",
        "claude-like": "/modes/build/prompt",
        "pi-like": "/loop/sequence",
        "opencode": "/tools/include",
        "oh-my-opencode": "/limits/max_turns",
        "unknown-name": "/sampling/temperature",
    }
    variants = tuple(
        {
            "variant_id": variant_id,
            "display_name": names[variant_id],
            "generated_name_receipt_ref": (
                _ref("generated-name-receipt") if variant_id == "unknown-name" else None
            ),
            "config_bundle_ref": _ref(f"bundle-{variant_id}"),
            "dependency_closure_ref": _ref(f"closure-{variant_id}"),
            "compiler_identity_ref": copy.deepcopy(compiler),
            "compiled_config_ref": _ref(f"compiled-{variant_id}"),
            "admission_receipt_ref": _ref(f"admission-{variant_id}"),
            "semantic_delta": {
                "name": f"delta-{variant_id}",
                "compiler_field_pointer": pointers[variant_id],
                "before_digest": _d(f"before-{variant_id}"),
                "after_digest": _d(f"after-{variant_id}"),
            },
            "optimizer_generated": variant_id == "unknown-name",
        }
        for variant_id in f4.VARIANT_IDS
    )
    variant_map = {variant["variant_id"]: variant for variant in variants}
    environment_map = {
        environment["environment_id"]: environment for environment in environments
    }

    episode_values: list[dict[str, object]] = []

    def episode(
        tag: str,
        variant_id: str,
        environment_id: str = "ibm-one-node",
        effective_plan_ref: dict[str, str] | None = None,
    ) -> dict[str, object]:
        variant = variant_map[variant_id]
        environment = environment_map[environment_id]
        value: dict[str, object] = {
            "target_attempt_id": f"attempt-{tag}",
            "episode_id": f"episode-{tag}",
            "environment_id": environment_id,
            "environment_ref": copy.deepcopy(environment["environment_ref"]),
            "target_attempt_output_ref": _ref(f"attempt-output-{tag}"),
            "episode_output_ref": _ref(f"episode-output-{tag}"),
            "target_report_output_ref": _ref("target-report-output"),
            "effective_plan_ref": effective_plan_ref or _ref(f"effective-plan-{tag}"),
            "compiled_config_ref": copy.deepcopy(variant["compiled_config_ref"]),
            "admission_receipt_ref": copy.deepcopy(variant["admission_receipt_ref"]),
            "invariant_identity": copy.deepcopy(identity),
            "attempt_state": "successful",
            "evidence_state": "current",
            "superseded_by_attempt_id": None,
            "target_report_ref": copy.deepcopy(target_report_ref),
            "evidence_manifest_ref": _ref(f"evidence-manifest-{tag}"),
            "completed_envelope_ref": _ref(f"completed-envelope-{tag}"),
            "closed_envelope_ref": _ref(f"closed-envelope-{tag}"),
            "tool_call_receipt_refs": (
                _ref(f"tool-call-{tag}-1"),
                _ref(f"tool-call-{tag}-2"),
            ),
            "server_verifier_result_ref": _ref(f"server-verifier-{tag}"),
            "terminal_outcome": "completed-and-closed",
            "verifier_passed": True,
            "reward": 1,
            "fallback_used": False,
            "fallback_variant_id": None,
            "cleanup": {
                "cleanup_receipt_ref": _ref(f"cleanup-{tag}"),
                "authoritative_closed": True,
                "active_lease_ids": (),
                "orphan_resource_ids": (),
                "leaked_artifact_ids": (),
                "cleanup_errors": (),
            },
        }
        episode_values.append(value)
        return value

    coverage = tuple(
        {
            "environment_id": environment["environment_id"],
            "variant_id": variant["variant_id"],
            "admission_receipt_ref": copy.deepcopy(variant["admission_receipt_ref"]),
            "evidence": episode(
                f"canary-{environment['environment_id']}-{variant['variant_id']}",
                variant["variant_id"],
                environment["environment_id"],
            ),
        }
        for environment in environments
        for variant in variants
    )
    base_plan_refs = {
        variant_id: _ref(f"base-effective-plan-{variant_id}")
        for variant_id in f4.VARIANT_IDS
    }
    variant_episodes = tuple(
        {
            "variant_id": variant_id,
            "evidence": episode(
                f"family-{variant_id}",
                variant_id,
                effective_plan_ref=copy.deepcopy(base_plan_refs[variant_id]),
            ),
        }
        for variant_id in f4.VARIANT_IDS
    )

    config_set_ref = _ref("weighted-config-set")
    selection_nonce = _d("selection-nonce-001")
    policy_capability_digest = _d("policy-capabilities")
    candidate_ids = ("claude-like", "codex-like")
    candidate_weights = (2, 5)
    candidates = tuple(
        {
            "variant_id": variant_id,
            "admission_receipt_ref": copy.deepcopy(
                variant_map[variant_id]["admission_receipt_ref"]
            ),
            "weight": weight,
            "ordered_overlay_receipt_refs": (),
        }
        for variant_id, weight in zip(candidate_ids, candidate_weights, strict=True)
    )
    draw_digest = _d(
        ""
    )  # overwritten by the frozen byte-level weighted-v1 computation below
    raw_draw = hashlib.sha256(
        b"bb-weighted-v1\0"
        + config_set_ref["digest"].encode()
        + selection_nonce.encode()
        + identity["task_contract_digest"].encode()
        + policy_capability_digest.encode()
    ).hexdigest()
    draw_digest = "sha256:" + raw_draw
    draw = int(raw_draw, 16) % sum(candidate_weights)
    selected_variant = (
        candidate_ids[0] if draw < candidate_weights[0] else candidate_ids[1]
    )
    weighted_selections = (
        {
            "selection_id": "weighted-ab-001",
            "config_set_ref": config_set_ref,
            "selection_nonce": selection_nonce,
            "task_contract_digest": identity["task_contract_digest"],
            "policy_capability_digest": policy_capability_digest,
            "candidates": candidates,
            "oracle_draw_digest": draw_digest,
            "oracle_selected_variant_id": selected_variant,
            "selection_record_ref": _ref("weighted-selection-record"),
            "persisted_before_lease": True,
            "fallback_used": False,
            "evidence": episode("weighted-ab-001", selected_variant),
        },
    )
    aa_controls = (
        {
            "control_id": "aa-control-001",
            "variant_id": "codex-like",
            "deterministic_input_digest": _d("aa-input"),
            "arm_a": {
                "evidence": episode(
                    "aa-control-001-arm-a",
                    "codex-like",
                    effective_plan_ref=copy.deepcopy(base_plan_refs["codex-like"]),
                ),
                "deterministic_output_digest": _d("aa-output"),
            },
            "arm_b": {
                "evidence": episode(
                    "aa-control-001-arm-b",
                    "codex-like",
                    effective_plan_ref=copy.deepcopy(base_plan_refs["codex-like"]),
                ),
                "deterministic_output_digest": _d("aa-output"),
            },
        },
    )
    overlay_executions = (
        {
            "overlay_execution_id": "overlay-001",
            "base_variant_id": "claude-like",
            "ordered_overlay_refs": (_ref("overlay-object-001"),),
            "overlay_admission_receipt_ref": _ref("overlay-admission-001"),
            "evidence": episode(
                "overlay-001",
                "claude-like",
                effective_plan_ref=_ref("overlay-effective-plan-001"),
            ),
        },
    )
    optimizer_variant = next(
        variant for variant in variants if variant["variant_id"] == "unknown-name"
    )
    optimizer_source_facts = {
        "schema_version": "bb.rl.phase5-f4-optimizer-source.v1",
        "source_member_path": "r-swe-001-terminal.json",
        "source_member_digest": _d("optimizer-source-member"),
        "config_bundle_ref": copy.deepcopy(
            optimizer_variant["config_bundle_ref"]
        ),
        "dependency_closure_ref": copy.deepcopy(
            optimizer_variant["dependency_closure_ref"]
        ),
        "compiler_identity_ref": copy.deepcopy(
            optimizer_variant["compiler_identity_ref"]
        ),
        "compiled_config_ref": copy.deepcopy(
            optimizer_variant["compiled_config_ref"]
        ),
        "admission_receipt_ref": copy.deepcopy(
            optimizer_variant["admission_receipt_ref"]
        ),
    }
    optimizer_facts = (
        {
            "schema_version": "bb.rl.phase5-f4-optimizer-generation.v1",
            "mutation_axis": "sampling",
            "generated_variant_id": "unknown-name",
            "parent_variant_id": "codex-like",
        },
        optimizer_source_facts,
        {
            "schema_version": "bb.rl.phase5-f4-optimizer-objective.v1",
            "primary_objective_frozen": True,
            "secondary_cost_frozen": True,
            "primary_improvement": 0.25,
            "secondary_cost_reduction": 0.0,
            "required_secondary_cost_reduction": 0.0,
        },
        {
            "schema_version": "bb.rl.phase5-f4-optimizer-constraints.v1",
            "non_config_inputs_identical": True,
            "correctness_regression": False,
            "security_regression": False,
            "isolation_regression": False,
            "evidence_regression": False,
            "cleanup_regression": False,
        },
        {
            "schema_version": "bb.rl.phase5-f4-optimizer-paired-ab.v1",
            "paired_ab_evaluation_count": 20,
        },
        {
            "schema_version": "bb.rl.phase5-f4-optimizer-aa-noise.v1",
            "aa_noise_upper_bound": 0.10,
        },
        {
            "schema_version": "bb.rl.phase5-f4-optimizer-held-out.v1",
            "held_out_repeated": True,
            "repeat_count": 1,
        },
        {
            "schema_version": "bb.rl.phase5-f4-optimizer-disposition.v1",
            "optimizer_acceptance_id": "optimizer-acceptance-001",
            "disposition": "accepted",
            "acceptance_basis": "improved-beyond-aa-noise",
        },
    )
    optimizer_receipts = []
    for sequence_index, (receipt_kind, facts) in enumerate(
        zip(
            (
                "generation-provenance",
                "source-member-identity",
                "objective",
                "constraints",
                "paired-ab-evaluations",
                "aa-noise-control",
                "held-out-repeat",
                "disposition",
            ),
            optimizer_facts,
            strict=True,
        )
    ):
        body = {
            "schema_version": "bb.rl.phase5-f4-optimizer-receipt.v1",
            "receipt_kind": receipt_kind,
            "sequence_index": sequence_index,
            "optimizer_acceptance_id": "optimizer-acceptance-001",
            "variant_id": "unknown-name",
            "parent_variant_id": "codex-like",
            "source_member_path": "r-swe-001-terminal.json",
            "source_member_digest": _d("optimizer-source-member"),
            "config_bundle_ref": copy.deepcopy(
                optimizer_variant["config_bundle_ref"]
            ),
            "dependency_closure_ref": copy.deepcopy(
                optimizer_variant["dependency_closure_ref"]
            ),
            "compiler_identity_ref": copy.deepcopy(
                optimizer_variant["compiler_identity_ref"]
            ),
            "compiled_config_ref": copy.deepcopy(
                optimizer_variant["compiled_config_ref"]
            ),
            "admission_receipt_ref": copy.deepcopy(
                optimizer_variant["admission_receipt_ref"]
            ),
            "facts": facts,
        }
        optimizer_receipts.append(
            {"ref": _content_ref(f"optimizer-{receipt_kind}", body), "artifact": body}
        )
    optimizer_packet = {
        "schema_version": "bb.rl.phase5-f4-optimizer-work-packet.v1",
        "optimizer_acceptance_id": "optimizer-acceptance-001",
        "variant_id": "unknown-name",
        "parent_variant_id": "codex-like",
        "ordered_receipts": tuple(optimizer_receipts),
    }
    optimizer_packet_binding = {
        "ref": _content_ref("optimizer-work-packet", optimizer_packet),
        "artifact": optimizer_packet,
    }
    optimizer_acceptances = (
        {
            "optimizer_acceptance_id": "optimizer-acceptance-001",
            "source_member_path": "r-swe-001-terminal.json",
            "source_member_digest": _d("optimizer-source-member"),
            "variant_id": "unknown-name",
            "parent_variant_id": "codex-like",
            "mutation_axis": "sampling",
            "optimizer_work_packet": optimizer_packet_binding,
            "paired_ab_evaluation_count": 20,
            "non_config_inputs_identical": True,
            "primary_objective_frozen": True,
            "secondary_cost_frozen": True,
            "correctness_regression": False,
            "security_regression": False,
            "isolation_regression": False,
            "evidence_regression": False,
            "cleanup_regression": False,
            "held_out_repeated": True,
            "acceptance_basis": "improved-beyond-aa-noise",
            "primary_improvement": 0.25,
            "aa_noise_upper_bound": 0.10,
            "secondary_cost_reduction": 0.0,
            "required_secondary_cost_reduction": 0.0,
            "evidence": episode("optimizer-acceptance-001", "unknown-name"),
        },
    )
    variants_by_compiled = {
        variant["compiled_config_ref"]["digest"]: variant["variant_id"]
        for variant in variants
    }
    target_report_artifact = {
        "schema_version": "bb.rl.phase5-f4-target-evidence-report.v1",
        "report_id": "f4-source-backed-target-report",
        "source_runtime_ref": _ref("approved-f3-source-runtime"),
        "executions": tuple(
            {
                "target_attempt_id": evidence["target_attempt_id"],
                "episode_id": evidence["episode_id"],
                "variant_id": variants_by_compiled[
                    evidence["compiled_config_ref"]["digest"]
                ],
                "environment_id": evidence["environment_id"],
                "environment_ref": copy.deepcopy(evidence["environment_ref"]),
                "source_runtime_ref": _ref("approved-f3-source-runtime"),
                "target_run_id": "f4-target-run",
                "target_job_id": "f4-target-job",
                "target_node_id": "f4-target-node",
                "invariant_identity": copy.deepcopy(evidence["invariant_identity"]),
                "target_attempt_output_ref": copy.deepcopy(
                    evidence["target_attempt_output_ref"]
                ),
                "episode_output_ref": copy.deepcopy(
                    evidence["episode_output_ref"]
                ),
                "target_report_output_ref": copy.deepcopy(
                    evidence["target_report_output_ref"]
                ),
                "compiled_config_ref": copy.deepcopy(evidence["compiled_config_ref"]),
                "admission_receipt_ref": copy.deepcopy(
                    evidence["admission_receipt_ref"]
                ),
                "effective_plan_ref": copy.deepcopy(evidence["effective_plan_ref"]),
                "evidence_manifest_ref": copy.deepcopy(
                    evidence["evidence_manifest_ref"]
                ),
                "completed_envelope_ref": copy.deepcopy(
                    evidence["completed_envelope_ref"]
                ),
                "closed_envelope_ref": copy.deepcopy(evidence["closed_envelope_ref"]),
                "tool_call_receipt_refs": copy.deepcopy(
                    evidence["tool_call_receipt_refs"]
                ),
                "server_verifier_result_ref": copy.deepcopy(
                    evidence["server_verifier_result_ref"]
                ),
                "verifier_passed": evidence["verifier_passed"],
                "reward": evidence["reward"],
                "cleanup_receipt_ref": copy.deepcopy(
                    evidence["cleanup"]["cleanup_receipt_ref"]
                ),
                "terminal_outcome": evidence["terminal_outcome"],
            }
            for evidence in episode_values
        ),
    }
    target_report_ref = _content_ref("target-report", target_report_artifact)
    for evidence in episode_values:
        evidence["target_report_ref"] = copy.deepcopy(target_report_ref)
    target_reports = ({"ref": target_report_ref, "artifact": target_report_artifact},)
    validity = {
        "not_before": "2026-07-14T00:00:00Z",
        "expires_at": "2026-07-15T00:00:00Z",
    }
    authority_artifact = {
        "schema_version": "bb.rl.phase5-f4-authority-root.v1",
        "compiler_identity_ref": copy.deepcopy(compiler),
        "admission_policy_ref": _ref("admission-policy"),
        "operator_ceiling_ref": _ref("operator-ceiling"),
        "runtime_abi": "breadboard-v2",
        "validity": validity,
    }
    authority_ref = _content_ref("authority-root", authority_artifact)
    authority_root = {"ref": authority_ref, "artifact": authority_artifact}
    receipt_refs = tuple(
        copy.deepcopy(variant["admission_receipt_ref"]) for variant in variants
    )
    ibm_root_artifact = {
        "schema_version": "bb.rl.phase5-f4-ibm-admission-set-root.v1",
        "authority_root_ref": copy.deepcopy(authority_ref),
        "compiler_identity_ref": copy.deepcopy(compiler),
        "admission_policy_ref": copy.deepcopy(
            authority_artifact["admission_policy_ref"]
        ),
        "operator_ceiling_ref": copy.deepcopy(
            authority_artifact["operator_ceiling_ref"]
        ),
        "runtime_abi": authority_artifact["runtime_abi"],
        "validity": copy.deepcopy(validity),
        "admission_receipt_refs": receipt_refs,
    }
    ibm_root = {
        "ref": _content_ref("ibm-admission-set-root", ibm_root_artifact, scheme="ibm"),
        "artifact": ibm_root_artifact,
    }
    l6_root_artifact = {
        "schema_version": "bb.rl.phase5-f4-l6-environment-set-root.v1",
        "authority_root_ref": copy.deepcopy(authority_ref),
        "environments": copy.deepcopy(environments),
    }
    l6_root = {
        "ref": _content_ref("l6-environment-set-root", l6_root_artifact),
        "artifact": l6_root_artifact,
    }
    return {
        "schema_version": "bb.rl.phase5-f4-campaign-input.v1",
        "campaign_id": "f4-r-swe-001-campaign",
        "authority_root": authority_root,
        "compiler_identity_ref": compiler,
        "ibm_admission_set_root": ibm_root,
        "l6_environment_set_root": l6_root,
        "evaluated_at": "2026-07-14T12:00:00Z",
        "target_reports": target_reports,
        "invariant_identity": identity,
        "environments": environments,
        "variants": variants,
        "ibm_admission_receipt_refs": receipt_refs,
        "environment_coverage": coverage,
        "variant_episodes": variant_episodes,
        "weighted_selections": weighted_selections,
        "aa_determinism_controls": aa_controls,
        "admitted_overlay_executions": overlay_executions,
        "optimizer_disposition": "accepted-variants",
        "accepted_optimizer_variants": optimizer_acceptances,
        "optimized_config_set_ref": _ref("optimized-config-set"),
    }


def _validated(payload: dict[str, object]) -> F4CampaignInput:
    return F4CampaignInput.model_validate(payload, strict=True)


def _assert_rejected(payload: dict[str, object], match: str | None = None) -> None:
    with pytest.raises((ValidationError, F4CampaignValidationError), match=match):
        _validated(payload)


def test_complete_campaign_validates_and_authors_canonical_atomic_outputs(
    tmp_path: Path,
) -> None:
    payload = _valid_payload()
    spec = _validated(payload)
    report = validate_f4_campaign(spec)
    assert report.disposition == "structurally-valid"
    assert report.claim_boundary == CLAIM_BOUNDARY
    assert len(report.checks) == 14
    assert all(check.structurally_valid for check in report.checks)
    assert len(spec.environment_coverage) == len(spec.variants) * len(spec.environments)
    assert len(spec.target_reports) == 1
    assert len(spec.target_reports[0].artifact.executions) == 23
    assert all(
        receipt.tool_call_receipt_refs
        and receipt.server_verifier_result_ref.digest.startswith("sha256:")
        and receipt.reward == 1
        for receipt in spec.target_reports[0].artifact.executions
    )
    assert all(
        len(selection.candidates) == 2
        and selection.persisted_before_lease is True
        and selection.fallback_used is False
        for selection in spec.weighted_selections
    )
    assert all(
        control.arm_a.deterministic_output_digest
        == control.arm_b.deterministic_output_digest
        for control in spec.aa_determinism_controls
    )
    assert all(
        execution.ordered_overlay_refs
        and execution.evidence.effective_plan_ref
        != next(
            row.evidence.effective_plan_ref
            for row in spec.variant_episodes
            if row.variant_id == execution.base_variant_id
        )
        for execution in spec.admitted_overlay_executions
    )
    assert len(
        {variant.semantic_delta.after_digest for variant in spec.variants}
    ) == len(f4.VARIANT_IDS)
    assert spec.optimizer_disposition == "accepted-variants"
    assert spec.accepted_optimizer_variants

    source = tmp_path / "campaign-input.json"
    source.write_bytes(canonical_json_bytes(payload))
    output = tmp_path / "f4-campaign"
    manifest_path = Path(author_f4_campaign(os.fspath(source), os.fspath(output)))

    assert manifest_path == output / "manifest.json"
    assert {path.name for path in output.iterdir()} == {
        "manifest.json",
        "validation-report.json",
    }
    manifest_raw = manifest_path.read_bytes()
    report_raw = (output / "validation-report.json").read_bytes()
    manifest = F4CampaignManifest.model_validate_json(manifest_raw, strict=True)
    written_report = F4ValidationReport.model_validate_json(report_raw, strict=True)
    assert canonical_json_bytes(manifest.model_dump(mode="json")) == manifest_raw
    assert canonical_json_bytes(written_report.model_dump(mode="json")) == report_raw
    assert manifest.campaign == spec
    assert (
        manifest.validation_report.digest
        == "sha256:" + hashlib.sha256(report_raw).hexdigest()
    )
    assert manifest.claim_boundary == CLAIM_BOUNDARY


def test_schema_print_cli_is_closed_and_canonical() -> None:
    project_root = Path(__file__).resolve().parents[3]
    completed = subprocess.run(
        [sys.executable, "scripts/rl_phase5/build_f4_campaign.py", "--print-schema"],
        cwd=project_root,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert completed.stdout.endswith(b"\n")
    schema_raw = completed.stdout[:-1]
    schema = json.loads(schema_raw)
    assert canonical_json_bytes(schema) == schema_raw
    assert schema["additionalProperties"] is False
    assert all(
        definition.get("additionalProperties") is False
        for definition in schema["$defs"].values()
    )
    assert set(schema["required"]) == set(schema["properties"])


def test_failed_authoring_never_publishes_partial_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _validated(_valid_payload())
    output = tmp_path / "campaign"
    original = f4._write_exclusive
    calls = 0

    def fail_second_write(directory_fd: int, name: str, payload: bytes) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected write failure")
        original(directory_fd, name, payload)

    monkeypatch.setattr(f4, "_write_exclusive", fail_second_write)
    with pytest.raises(OSError, match="injected write failure"):
        f4.build_f4_campaign(spec, os.fspath(output))
    assert not output.exists()
    assert not tuple(tmp_path.glob(".campaign.authoring-*"))


@pytest.mark.parametrize("missing", ["environment", "variant"])
def test_missing_environment_or_variant_rejects(missing: str) -> None:
    payload = _valid_payload()
    if missing == "environment":
        payload["environment_coverage"] = payload["environment_coverage"][:-1]
    else:
        payload["variants"] = payload["variants"][:-1]
    _assert_rejected(payload)


def test_duplicate_compiled_config_digest_rejects() -> None:
    payload = _valid_payload()
    variants = list(payload["variants"])
    variants[1] = copy.deepcopy(variants[1])
    variants[1]["compiled_config_ref"] = copy.deepcopy(
        variants[0]["compiled_config_ref"]
    )
    payload["variants"] = tuple(variants)
    _assert_rejected(payload, "compiled config identities must be unique")


def test_unnamed_semantic_delta_rejects() -> None:
    payload = _valid_payload()
    variants = list(payload["variants"])
    variants[0] = copy.deepcopy(variants[0])
    variants[0]["semantic_delta"]["name"] = ""
    payload["variants"] = tuple(variants)
    _assert_rejected(payload)


def test_non_config_identity_drift_rejects() -> None:
    payload = _valid_payload()
    rows = list(payload["variant_episodes"])
    rows[2] = copy.deepcopy(rows[2])
    rows[2]["evidence"]["invariant_identity"]["model_ref"] = _ref("drifted-model")
    payload["variant_episodes"] = tuple(rows)
    _assert_rejected(payload, "non-config campaign identity drift")


def test_wrong_weighted_choice_rejects() -> None:
    payload = _valid_payload()
    selections = list(payload["weighted_selections"])
    selections[0] = copy.deepcopy(selections[0])
    selected = selections[0]["oracle_selected_variant_id"]
    selections[0]["oracle_selected_variant_id"] = (
        "claude-like" if selected == "codex-like" else "codex-like"
    )
    payload["weighted_selections"] = tuple(selections)
    _assert_rejected(payload, "weighted selection does not match")


def test_fallback_rejects() -> None:
    payload = _valid_payload()
    rows = list(payload["variant_episodes"])
    rows[0] = copy.deepcopy(rows[0])
    rows[0]["evidence"]["fallback_used"] = True
    payload["variant_episodes"] = tuple(rows)
    _assert_rejected(payload)


def test_missing_overlay_or_optimizer_receipt_rejects() -> None:
    payload = _valid_payload()
    overlays = list(payload["admitted_overlay_executions"])
    overlays[0] = copy.deepcopy(overlays[0])
    del overlays[0]["overlay_admission_receipt_ref"]
    payload["admitted_overlay_executions"] = tuple(overlays)
    _assert_rejected(payload)

    payload = _valid_payload()
    accepted = list(payload["accepted_optimizer_variants"])
    accepted[0] = copy.deepcopy(accepted[0])
    receipts = list(
        accepted[0]["optimizer_work_packet"]["artifact"]["ordered_receipts"]
    )
    del receipts[0]
    accepted[0]["optimizer_work_packet"]["artifact"]["ordered_receipts"] = tuple(
        receipts
    )
    payload["accepted_optimizer_variants"] = tuple(accepted)
    _assert_rejected(payload)


@pytest.mark.parametrize(
    "attack",
    [
        "coherent-body-tamper",
        "ref-tamper",
        "reorder",
        "all-source-substitution",
        "recompilation-substitution",
    ],
)
def test_optimizer_work_packet_is_exact_and_source_closed(attack: str) -> None:
    payload = _valid_payload()
    accepted = payload["accepted_optimizer_variants"][0]
    binding = accepted["optimizer_work_packet"]
    packet = binding["artifact"]
    receipts = list(packet["ordered_receipts"])

    def rehash_packet() -> None:
        for index, receipt in enumerate(receipts):
            receipt["ref"] = _content_ref(
                f"optimizer-rehashed-{index}", receipt["artifact"]
            )
        packet["ordered_receipts"] = tuple(receipts)
        binding["ref"] = _content_ref("optimizer-work-packet-rehashed", packet)

    if attack == "coherent-body-tamper":
        receipts[2]["artifact"]["facts"]["primary_improvement"] = 0.5
        rehash_packet()
    elif attack == "ref-tamper":
        receipts[0]["ref"] = _ref("substituted-optimizer-receipt")
    elif attack == "reorder":
        receipts[0], receipts[1] = receipts[1], receipts[0]
        rehash_packet()
    elif attack == "all-source-substitution":
        for receipt in receipts:
            receipt["artifact"]["source_member_path"] = "substituted.json"
            receipt["artifact"]["source_member_digest"] = _d("substituted-source")
        source_facts = receipts[1]["artifact"]["facts"]
        source_facts["source_member_path"] = "substituted.json"
        source_facts["source_member_digest"] = _d("substituted-source")
        rehash_packet()
    else:
        replacement_fields = (
            "config_bundle_ref",
            "dependency_closure_ref",
            "compiler_identity_ref",
            "compiled_config_ref",
            "admission_receipt_ref",
        )
        for field in replacement_fields:
            replacement = _ref(f"substituted-{field}")
            for receipt in receipts:
                receipt["artifact"][field] = copy.deepcopy(replacement)
            receipts[1]["artifact"]["facts"][field] = copy.deepcopy(replacement)
        rehash_packet()
    _assert_rejected(payload)


@pytest.mark.parametrize(
    ("field", "value"),
    [("evidence_state", "stale"), ("attempt_state", "failed")],
)
def test_stale_or_failed_attempt_rejects(field: str, value: str) -> None:
    payload = _valid_payload()
    rows = list(payload["environment_coverage"])
    rows[0] = copy.deepcopy(rows[0])
    rows[0]["evidence"][field] = value
    payload["environment_coverage"] = tuple(rows)
    _assert_rejected(payload)


def test_cross_arm_episode_reuse_rejects() -> None:
    payload = _valid_payload()
    controls = list(payload["aa_determinism_controls"])
    controls[0] = copy.deepcopy(controls[0])
    controls[0]["arm_b"]["evidence"]["episode_id"] = controls[0]["arm_a"]["evidence"][
        "episode_id"
    ]
    payload["aa_determinism_controls"] = tuple(controls)
    _assert_rejected(payload, "cross-arm reuse")


def test_cleanup_leak_rejects() -> None:
    payload = _valid_payload()
    rows = list(payload["variant_episodes"])
    rows[0] = copy.deepcopy(rows[0])
    rows[0]["evidence"]["cleanup"]["orphan_resource_ids"] = ("orphan-1",)
    payload["variant_episodes"] = tuple(rows)
    _assert_rejected(payload, "cleanup is incomplete")


@pytest.mark.parametrize(
    ("field", "value"),
    [("reward", 0), ("terminal_outcome", "failed")],
)
def test_false_reward_or_unexpected_outcome_rejects(field: str, value: object) -> None:
    payload = _valid_payload()
    rows = list(payload["variant_episodes"])
    rows[0] = copy.deepcopy(rows[0])
    rows[0]["evidence"][field] = value
    payload["variant_episodes"] = tuple(rows)
    _assert_rejected(payload)


def test_aa_disagreement_rejects() -> None:
    payload = _valid_payload()
    controls = list(payload["aa_determinism_controls"])
    controls[0] = copy.deepcopy(controls[0])
    controls[0]["arm_b"]["deterministic_output_digest"] = _d("different-aa-output")
    payload["aa_determinism_controls"] = tuple(controls)
    _assert_rejected(payload, "A/A deterministic controls disagree")


def test_mutable_reference_and_extra_secret_field_reject() -> None:
    mutable = _valid_payload()
    mutable["authority_root_ref"] = {
        "reference": "evidence://authority-root/latest",
        "digest": _d("authority-root"),
    }
    _assert_rejected(mutable)

    secret = _valid_payload()
    secret["secret_value"] = "must-never-enter-the-model"
    _assert_rejected(secret)


def _rebind_target_report(payload: dict[str, object]) -> None:
    binding = payload["target_reports"][0]
    report_ref = _content_ref("target-report", binding["artifact"])
    binding["ref"] = report_ref

    def replace(value: object) -> None:
        if isinstance(value, dict):
            if "target_report_ref" in value:
                value["target_report_ref"] = copy.deepcopy(report_ref)
            for child in value.values():
                replace(child)
        elif isinstance(value, (list, tuple)):
            for child in value:
                replace(child)

    replace(payload)


def test_content_bound_roots_and_validity_reject_substitution() -> None:
    substituted = _valid_payload()
    substituted["authority_root"]["ref"] = _ref("syntactically-valid-substitute-root")
    _assert_rejected(substituted, "authority root digest")

    expired = _valid_payload()
    expired["evaluated_at"] = "2026-07-16T00:00:00Z"
    _assert_rejected(expired, "outside authority validity")


def test_source_report_receipt_mismatch_and_unused_execution_reject() -> None:
    mismatch = _valid_payload()
    mismatch_receipt = mismatch["target_reports"][0]["artifact"]["executions"][0]
    mismatch_receipt["effective_plan_ref"] = _ref("substituted-effective-plan")
    _rebind_target_report(mismatch)
    _assert_rejected(mismatch, "source-backed target receipt")

    unused = _valid_payload()
    extra = copy.deepcopy(unused["target_reports"][0]["artifact"]["executions"][0])
    extra["episode_id"] = "episode-unused-target-row"
    extra["target_attempt_id"] = "attempt-unused-target-row"
    extra["target_attempt_output_ref"] = _ref("unused-target-attempt-output")
    extra["episode_output_ref"] = _ref("unused-episode-output")
    unused["target_reports"][0]["artifact"]["executions"] += (extra,)
    _rebind_target_report(unused)
    _assert_rejected(unused, "cover exactly every campaign execution")


def test_tool_receipt_and_exact_optimizer_sentinel_are_mandatory() -> None:
    no_tool = _valid_payload()
    no_tool["environment_coverage"][0]["evidence"]["tool_call_receipt_refs"] = ()
    _assert_rejected(no_tool)

    legacy_sentinel = _valid_payload()
    legacy_sentinel["optimizer_disposition"] = "no-variant-accepted"
    _assert_rejected(legacy_sentinel)


def test_noncanonical_input_rejects_before_publication(tmp_path: Path) -> None:
    source = tmp_path / "campaign-input.json"
    source.write_text(json.dumps(_valid_payload(), indent=2), encoding="utf-8")
    output = tmp_path / "campaign"
    with pytest.raises(F4CampaignValidationError, match="canonical JSON"):
        author_f4_campaign(os.fspath(source), os.fspath(output))
    assert not output.exists()
