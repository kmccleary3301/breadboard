from __future__ import annotations

import json
from pathlib import Path


def test_run_stage6_broader_transfer_canary_emits_stage6_surfaces(monkeypatch, tmp_path: Path) -> None:
    import scripts.run_darwin_stage6_broader_transfer_canary_v0 as module

    out_dir = Path(module.ROOT) / "artifacts" / "test_tmp" / tmp_path.name / "stage6_canary"

    monkeypatch.setattr(
        module,
        "build_stage6_family_activation_rows",
        lambda: [
            {
                "family_id": "family.systems.policy",
                "lane_id": "lane.systems",
                "family_kind": "policy",
                "activation_enabled": True,
                "transfer_targets": ["lane.scheduling"],
                "activation_status": "active",
                "lane_weight": "primary_proving_lane",
            },
            {
                "family_id": "family.repo.topology",
                "lane_id": "lane.repo_swe",
                "family_kind": "topology",
                "activation_enabled": True,
                "transfer_targets": ["lane.systems"],
                "activation_status": "challenge",
                "lane_weight": "challenge_lane",
            },
        ],
    )
    monkeypatch.setattr(
        module,
        "build_stage6_search_policy_preview",
        lambda *, lane_id, budget_class="class_a": {
            "lane_id": lane_id,
            "campaign_class": "C1 Discovery",
            "repetition_count": 1,
        },
    )
    monkeypatch.setattr(
        module,
        "select_stage6_search_policy_arms",
        lambda *, search_policy, candidate_rows: [
            {
                "campaign_arm_id": f"arm.{search_policy['lane_id']}.warm",
                "lane_id": search_policy["lane_id"],
                "operator_id": "mut.policy.shadow_memory_enable_v1" if search_policy["lane_id"] == "lane.systems" else "mut.topology.single_to_pev_v1",
                "comparison_mode": "warm_start",
                "family_context": {"allowed_family_ids": ["family.systems.policy"], "blocked_family_ids": []},
                "search_policy_selection": {"lane_weight": "primary_proving_lane" if search_policy["lane_id"] == "lane.systems" else "challenge_lane"},
            },
            {
                "campaign_arm_id": f"arm.{search_policy['lane_id']}.single",
                "lane_id": search_policy["lane_id"],
                "operator_id": "mut.policy.shadow_memory_enable_v1" if search_policy["lane_id"] == "lane.systems" else "mut.topology.single_to_pev_v1",
                "comparison_mode": "single_family_lockout",
                "family_context": {"allowed_family_ids": [], "blocked_family_ids": ["family.systems.policy"]},
                "search_policy_selection": {"lane_weight": "primary_proving_lane" if search_policy["lane_id"] == "lane.systems" else "challenge_lane"},
            },
        ],
    )
    monkeypatch.setattr(module, "_campaign_lookup", lambda: {"lane.systems": {"lane_id": "lane.systems"}, "lane.repo_swe": {"lane_id": "lane.repo_swe"}})
    monkeypatch.setattr(module, "_candidate_universe", lambda **_: [])

    def fake_run_arm(*, arm_cfg, spec, out_dir):
        out_dir.mkdir(parents=True, exist_ok=True)
        operator_id = str(arm_cfg["operator_id"])
        comparison_mode = str(arm_cfg["comparison_mode"])
        runtime_ms = -20 if comparison_mode == "warm_start" else -4
        return (
            dict(arm_cfg),
            [
                {
                    "campaign_arm_id": arm_cfg["campaign_arm_id"],
                    "lane_id": arm_cfg["lane_id"],
                    "operator_id": operator_id,
                    "repetition_index": 1,
                    "comparison_valid": True,
                    "claim_eligible": True,
                    "delta_score": 0.0,
                    "delta_runtime_ms": runtime_ms,
                    "delta_cost_usd": 0.0,
                        "comparison_mode": comparison_mode,
                        "family_context": dict(arm_cfg.get("family_context") or {}),
                        "evaluator_pack_version": "stage4.evalpack.v1",
                        "comparison_envelope_digest": "a" * 64,
                        "execution_mode": "live",
                        "requested_route_id": "openrouter/gpt-5.4-mini",
                        "route_id": "openai/gpt-5.4-mini",
                        "route_class": "default_worker",
                        "provider_model": "openai/gpt-5.4-mini",
                        "cost_source": "estimated_from_pricing_table",
                        "provider_origin": "openai",
                        "search_policy_selection": dict(arm_cfg.get("search_policy_selection") or {}),
                    }
                ],
                [
                    {
                        "requested_provider_origin": "openrouter",
                        "provider_origin": "openai",
                        "route_class": "default_worker",
                        "execution_mode": "live",
                        "cost_source": "estimated_from_pricing_table",
                        "fallback_reason": "openrouter_http_401",
                    }
                ],
            )

    monkeypatch.setattr(module, "_run_arm", fake_run_arm)
    monkeypatch.setattr(module, "build_stage4_live_comparisons", lambda rows: rows)

    summary = module.run_stage6_broader_transfer_canary(out_dir=out_dir)
    payload = json.loads(Path(summary["summary_path"]).read_text(encoding="utf-8"))
    assert payload["single_family_lockout_comparison_count"] == 2
    assert payload["transfer_case_count"] >= 1
    assert payload["activation_probe_count"] >= 1
    assert payload["provider_segmentation_status"] == "claim_rows_segmented"
    assert payload["claim_rows_have_canonical_provider_segmentation"] is True
