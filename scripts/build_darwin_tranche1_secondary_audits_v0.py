from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from breadboard_ext.darwin.contracts import validate_effective_policy, validate_evaluator_pack
from breadboard_ext.darwin.phase2 import build_effective_policy, build_evaluator_pack
from breadboard_ext.darwin.stage4 import (
    build_stage4_budget_envelope,
    build_stage4_comparison_envelope_digest,
    build_stage4_support_envelope_digest,
    stage4_evaluator_pack_version,
)


BOOTSTRAP_MANIFEST = ROOT / "artifacts" / "darwin" / "bootstrap" / "bootstrap_manifest_v0.json"
OUT_DIR = ROOT / "artifacts" / "darwin" / "tranche1_audits"
AUDIT_LANES = ["lane.scheduling", "lane.atp"]
AUDIT_TASK_IDS = {
    "lane.scheduling": "task.darwin.scheduling.constraint_objective_smoke.audit",
    "lane.atp": "task.darwin.atp.ops_digest.audit",
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _campaign_lookup() -> dict[str, dict]:
    manifest = _load_json(BOOTSTRAP_MANIFEST)
    rows: dict[str, dict] = {}
    for row in manifest.get("specs") or []:
        spec = _load_json(ROOT / row["path"])
        rows[spec["lane_id"]] = spec
    return rows


def write_secondary_audits(out_dir: Path = OUT_DIR) -> dict:
    campaigns = _campaign_lookup()
    rows: list[dict] = []
    for lane_id in AUDIT_LANES:
        spec = campaigns[lane_id]
        lane_dir = out_dir / lane_id
        candidate_id = f"cand.{lane_id}.audit.shadow.v1"
        effective_policy = build_effective_policy(
            spec=spec,
            lane_id=lane_id,
            candidate_id=candidate_id,
            trial_label="audit_shadow",
            topology_id=spec["topology_family"],
            policy_bundle_id=spec["policy_bundle_id"],
            budget_class=spec["budget_class"],
        )
        evaluator_pack = build_evaluator_pack(
            spec=spec,
            lane_id=lane_id,
            candidate_id=candidate_id,
            trial_label="audit_shadow",
            task_id=AUDIT_TASK_IDS[lane_id],
            budget_class=spec["budget_class"],
        )
        evaluator_pack["pack_version"] = stage4_evaluator_pack_version(
            lane_id=lane_id,
            task_id=AUDIT_TASK_IDS[lane_id],
        )
        support_envelope_digest = build_stage4_support_envelope_digest(
            lane_id=lane_id,
            task_id=AUDIT_TASK_IDS[lane_id],
            topology_id=spec["topology_family"],
            policy_bundle_id=spec["policy_bundle_id"],
            budget_class=spec["budget_class"],
            allowed_tools=list(spec.get("allowed_tools") or []),
            environment_digest=str(spec.get("environment_digest") or "unknown-environment"),
            claim_target=str(spec.get("claim_target") or "internal"),
        )
        comparison_envelope_digest = build_stage4_comparison_envelope_digest(
            lane_id=lane_id,
            task_id=AUDIT_TASK_IDS[lane_id],
            budget_class=spec["budget_class"],
            comparison_class="bounded_internal",
            environment_digest=str(spec.get("environment_digest") or "unknown-environment"),
            claim_target=str(spec.get("claim_target") or "internal"),
        )
        evaluator_pack["budget_envelope"] = build_stage4_budget_envelope(
            budget_class=spec["budget_class"],
            wall_clock_ms=0,
            token_counts={},
            cost_estimate=0.0,
            comparison_class="bounded_internal",
            route_id=None,
            provider_model=None,
            execution_mode="scaffold",
            route_class="local_baseline",
            cost_source="local_execution",
            support_envelope_digest=support_envelope_digest,
            comparison_envelope_digest=comparison_envelope_digest,
            evaluator_pack_version=evaluator_pack["pack_version"],
            replication_reserve_fraction=0.2,
            control_reserve_fraction=0.1,
        )
        policy_issues = validate_effective_policy(effective_policy)
        evaluator_issues = validate_evaluator_pack(evaluator_pack)
        if policy_issues or evaluator_issues:
            issues = policy_issues + evaluator_issues
            raise ValueError("; ".join(f"{issue.path}: {issue.message}" for issue in issues))
        policy_path = lane_dir / "audit_effective_policy_v0.json"
        evaluator_path = lane_dir / "audit_evaluator_pack_v0.json"
        _write_json(policy_path, effective_policy)
        _write_json(evaluator_path, evaluator_pack)
        rows.append(
            {
                "lane_id": lane_id,
                "effective_policy_ref": str(policy_path.relative_to(ROOT)),
                "evaluator_pack_ref": str(evaluator_path.relative_to(ROOT)),
                "topology_supported": effective_policy["topology_support"]["is_supported"],
                "invalid_rule_count": len(evaluator_pack["invalid_comparison_rules"]),
            }
        )
    summary = {
        "schema": "breadboard.darwin.tranche1_secondary_audit_summary.v0",
        "lane_count": len(rows),
        "lanes": rows,
    }
    summary_path = out_dir / "secondary_audit_summary_v0.json"
    _write_json(summary_path, summary)
    return {"summary_path": str(summary_path), "lane_count": len(rows)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Emit DARWIN tranche-1 secondary audit policy/evaluator artifacts.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = write_secondary_audits()
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"secondary_audit_summary={summary['summary_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
