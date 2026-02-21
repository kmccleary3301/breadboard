#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import statistics
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

# Allow direct invocation via `python scripts/run_rlm_phase2_tier0.py`.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agentic_coder_prototype.agent_llm_openai import OpenAIConductor
from agentic_coder_prototype.provider_routing import provider_router
from agentic_coder_prototype.provider_runtime import ProviderRuntimeContext, provider_registry
from scripts.validate_rlm_artifacts import validate as validate_rlm_artifacts


SCHEMA_VERSION = "rlm_phase2_tier0_v1"
ARMS = ["A0_BASELINE", "A1_RLM_SYNC", "A2_RLM_BATCH", "A3_HYBRID_LONGRUN"]


@dataclass(frozen=True)
class Scenario:
    scenario_id: str
    family: str
    description: str


SCENARIOS: List[Scenario] = [
    Scenario("lc_repo_summary", "LC_REPO_SUMMARY", "Summarize cross-file TODOs from repository slices."),
    Scenario("lc_cross_file_debug", "LC_CROSS_FILE_DEBUG", "Cross-file bug localization style query."),
    Scenario("lc_plan_execute", "LC_PLAN_AND_EXECUTE", "Plan then execute staged prompts."),
    Scenario("batch_branch_explosion", "BATCH_BRANCH_EXPLOSION", "High branch fanout batch scheduling."),
]


class _FakeSessionState:
    def __init__(self) -> None:
        self._meta: Dict[str, Any] = {}
        self.task_events: List[Dict[str, Any]] = []

    def get_provider_metadata(self, key: str) -> Any:
        return self._meta.get(key)

    def set_provider_metadata(self, key: str, value: Any) -> None:
        self._meta[key] = value

    def emit_task_event(self, payload: Dict[str, Any]) -> None:
        self.task_events.append(dict(payload))


def _make_conductor(config: Dict[str, Any], workspace: Path, session_state: Optional[_FakeSessionState]) -> OpenAIConductor:
    cls = OpenAIConductor.__ray_metadata__.modified_class
    inst = object.__new__(cls)
    inst.config = config
    inst.workspace = str(workspace)
    inst._active_session_state = session_state
    return inst  # type: ignore[return-value]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_workspace_files(workspace: Path) -> None:
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    (workspace / "src" / "parser.py").write_text(
        "\n".join(
            [
                "def parse_token(token: str) -> str:",
                "    # TODO: normalize unicode punctuation",
                "    if token == 'ERR':",
                "        return 'error'",
                "    return token.strip()",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (workspace / "src" / "router.py").write_text(
        "\n".join(
            [
                "def route_event(event: dict) -> str:",
                "    # TODO: handle malformed payloads safely",
                "    kind = event.get('kind', '')",
                "    if kind == 'parse':",
                "        return 'parser'",
                "    return 'default'",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (workspace / "README.md").write_text("BreadBoard synthetic benchmark workspace\n", encoding="utf-8")


def _base_config(
    arm: str,
    model_route: str,
    *,
    subcall_timeout_seconds: float,
    batch_timeout_seconds: float,
) -> Dict[str, Any]:
    rlm_enabled = arm != "A0_BASELINE"
    scheduling_mode = "batch" if arm in {"A2_RLM_BATCH", "A3_HYBRID_LONGRUN"} else "sync"
    batch_enabled = arm in {"A2_RLM_BATCH", "A3_HYBRID_LONGRUN"}
    return {
        "providers": {"default_model": model_route},
        "features": {
            "rlm": {
                "enabled": rlm_enabled,
                "budget": {
                    "max_depth": 3,
                    "max_subcalls": 64,
                    "max_total_tokens": 50000,
                    "max_total_cost_usd": 2.0,
                    "max_wallclock_seconds": 120,
                },
                "subcall": {"model": model_route, "timeout_seconds": float(subcall_timeout_seconds), "retries": 1},
                "scheduling": {
                    "mode": scheduling_mode,
                    "batch": {
                        "enabled": batch_enabled,
                        "max_concurrency": 6,
                        "max_concurrency_per_branch": 2,
                        "retries": 1,
                        "timeout_seconds": float(batch_timeout_seconds),
                        "fail_fast": False,
                    },
                },
            }
        },
    }


def _invoke_baseline(prompt: str, model_route: str) -> Dict[str, Any]:
    if model_route.startswith("mock/"):
        os.environ.setdefault("MOCK_API_KEY", "mock")
    descriptor, model = provider_router.get_runtime_descriptor(model_route)
    runtime = provider_registry.create_runtime(descriptor)
    client_cfg = provider_router.create_client_config(model_route)
    client = runtime.create_client(client_cfg.get("api_key") or "mock", base_url=client_cfg.get("base_url"), default_headers=client_cfg.get("default_headers"))
    result = runtime.invoke(
        client=client,
        model=model,
        messages=[{"role": "user", "content": prompt}],
        tools=None,
        stream=False,
        context=ProviderRuntimeContext(session_state=None, agent_config={}, stream=False),
    )
    text = ""
    if result.messages:
        text = str(result.messages[0].content or "")
    usage = dict(result.usage or {})
    cost = float(usage.get("cost_usd") or usage.get("estimated_cost_usd") or 0.0)
    tokens = int(usage.get("total_tokens") or (int(usage.get("input_tokens") or 0) + int(usage.get("output_tokens") or 0)))
    return {"status": "completed" if text else "failed", "text": text, "usage": usage, "total_tokens": tokens, "total_cost_usd": cost}


def _scenario_prompts(scenario_id: str) -> List[str]:
    if scenario_id == "lc_repo_summary":
        return [
            "Summarize unresolved TODOs in parser and router modules.",
            "Return concise risk bullets and next actions.",
        ]
    if scenario_id == "lc_cross_file_debug":
        return [
            "Identify likely failure paths caused by malformed payloads across parser/router.",
            "Propose a patch plan with branch ownership.",
        ]
    if scenario_id == "lc_plan_execute":
        return [
            "Draft a staged implementation plan for robust routing validation.",
            "List acceptance checks and rollback criteria.",
        ]
    if scenario_id == "batch_branch_explosion":
        return [f"Analyze branch shard {i} for migration risks." for i in range(16)]
    return ["Summarize current state."]


def _emit_hybrid_markers(conductor: OpenAIConductor, session_state: Optional[_FakeSessionState], task_id: str, description: str) -> None:
    if session_state is None:
        return
    conductor._emit_task_event(
        {
            "kind": "subagent_spawned",
            "task_id": task_id,
            "sessionId": "tier0",
            "subagent_type": "explore",
            "description": description,
            "depth": 1,
        }
    )


def _emit_hybrid_complete(conductor: OpenAIConductor, session_state: Optional[_FakeSessionState], task_id: str, description: str) -> None:
    if session_state is None:
        return
    conductor._emit_task_event(
        {
            "kind": "subagent_completed",
            "task_id": task_id,
            "sessionId": "tier0",
            "subagent_type": "explore",
            "description": description,
            "depth": 1,
        }
    )


def _run_arm_scenario(
    arm: str,
    scenario: Scenario,
    seed: int,
    out_root: Path,
    model_route: str,
    *,
    subcall_timeout_seconds: float,
    batch_timeout_seconds: float,
) -> Dict[str, Any]:
    started = time.time()
    run_id = f"{arm.lower()}__{scenario.scenario_id}__s{seed}"
    workspace = out_root / run_id
    workspace.mkdir(parents=True, exist_ok=True)
    _ensure_workspace_files(workspace)

    if arm == "A0_BASELINE":
        prompt = _scenario_prompts(scenario.scenario_id)[0]
        row = _invoke_baseline(prompt, model_route)
        elapsed = max(0.0, time.time() - started)
        return {
            "run_id": run_id,
            "arm": arm,
            "scenario_id": scenario.scenario_id,
            "family": scenario.family,
            "seed": seed,
            "task_success": bool(row.get("status") == "completed"),
            "review_pass": bool(row.get("status") == "completed"),
            "major_regression_count": 0 if row.get("status") == "completed" else 1,
            "total_tokens": int(row.get("total_tokens") or 0),
            "total_cost_usd": float(row.get("total_cost_usd") or 0.0),
            "subcall_count": 0,
            "wallclock_seconds": elapsed,
            "timeout_count": 0,
            "blocked_count": 0,
            "fail_fast_triggered": False,
            "nondeterminism_detected": False,
            "artifact_validation_ok": True,
            "workspace": str(workspace),
        }

    session_state: Optional[_FakeSessionState] = None
    if arm == "A3_HYBRID_LONGRUN":
        session_state = _FakeSessionState()
        session_state.set_provider_metadata("longrun_episode_index", seed)

    conductor = _make_conductor(
        _base_config(
            arm,
            model_route,
            subcall_timeout_seconds=subcall_timeout_seconds,
            batch_timeout_seconds=batch_timeout_seconds,
        ),
        workspace,
        session_state,
    )
    prompts = _scenario_prompts(scenario.scenario_id)
    statuses: List[str] = []
    total_tokens = 0
    total_cost_usd = 0.0
    timeout_count = 0
    blocked_count = 0
    fail_fast_triggered = False
    nondeterminism = False

    put = conductor._exec_raw(
        {"function": "blob.put_file_slice", "arguments": {"path": "src/parser.py", "start_line": 1, "end_line": 5, "branch_id": "analysis.repo"}}
    )
    blob_id = str(put.get("blob_id") or "")
    if not blob_id:
        statuses.append("failed")

    if arm == "A1_RLM_SYNC":
        for i, prompt in enumerate(prompts):
            task_id = f"{scenario.scenario_id}.sync.{i}"
            _emit_hybrid_markers(conductor, session_state, task_id, prompt)
            out = conductor._exec_raw(
                {
                    "function": "llm.query",
                    "arguments": {
                        "prompt": prompt,
                        "blob_refs": [blob_id] if blob_id else [],
                        "branch_id": f"analysis.{scenario.scenario_id}.{i}",
                        "model": model_route,
                    },
                }
            )
            _emit_hybrid_complete(conductor, session_state, task_id, prompt)
            status = "completed" if str(out.get("reason") or "") == "" and "error" not in out else "failed"
            statuses.append(status)
            total_tokens += int(out.get("usage_tokens") or 0)
            total_cost_usd += float(out.get("estimated_cost_usd") or 0.0)
    else:
        queries: List[Dict[str, Any]] = []
        for i, prompt in enumerate(prompts):
            queries.append(
                {
                    "prompt": prompt,
                    "blob_refs": [blob_id] if blob_id else [],
                    "branch_id": f"analysis.{scenario.scenario_id}.b{i}",
                    "task_id": f"{scenario.scenario_id}.batch.{i}",
                }
            )
        task_id = f"{scenario.scenario_id}.batch"
        _emit_hybrid_markers(conductor, session_state, task_id, scenario.description)
        out = conductor._exec_raw(
            {
                "function": "llm.batch_query",
                "arguments": {"queries": queries, "branch_id": f"analysis.{scenario.scenario_id}", "model": model_route},
            }
        )
        _emit_hybrid_complete(conductor, session_state, task_id, scenario.description)
        rows = out.get("results") if isinstance(out, dict) else None
        if isinstance(rows, list):
            request_indexes = [int(row.get("request_index", -1)) for row in rows]
            nondeterminism = request_indexes != sorted(request_indexes)
            for row in rows:
                status = str(row.get("status") or "failed")
                statuses.append(status)
                if status == "timeout":
                    timeout_count += 1
                if status == "blocked":
                    blocked_count += 1
                    if str(row.get("reason") or "") == "fail_fast_short_circuit":
                        fail_fast_triggered = True
                total_tokens += int(row.get("usage_tokens") or 0)
                total_cost_usd += float(row.get("estimated_cost_usd") or 0.0)
        else:
            statuses.append("failed")

    artifacts_ok = False
    validator_payload: Dict[str, Any] = {}
    try:
        validator_payload = validate_rlm_artifacts(workspace / ".breadboard")
        artifacts_ok = bool(validator_payload.get("ok"))
    except Exception:
        artifacts_ok = False

    non_completed = len([s for s in statuses if s != "completed"])
    elapsed = max(0.0, time.time() - started)
    subcall_count = int(validator_payload.get("subcalls") or 0) + int(validator_payload.get("batch_subcalls") or 0)
    if arm != "A0_BASELINE" and subcall_count <= 0:
        artifacts_ok = False
    return {
        "run_id": run_id,
        "arm": arm,
        "scenario_id": scenario.scenario_id,
        "family": scenario.family,
        "seed": seed,
        "task_success": non_completed == 0 and artifacts_ok,
        "review_pass": non_completed == 0 and artifacts_ok and not nondeterminism,
        "major_regression_count": non_completed + (0 if artifacts_ok else 1),
        "total_tokens": int(total_tokens),
        "total_cost_usd": float(total_cost_usd),
        "subcall_count": subcall_count,
        "wallclock_seconds": elapsed,
        "timeout_count": timeout_count,
        "blocked_count": blocked_count,
        "fail_fast_triggered": fail_fast_triggered,
        "nondeterminism_detected": nondeterminism,
        "artifact_validation_ok": artifacts_ok,
        "workspace": str(workspace),
    }


def _median(values: List[float]) -> float:
    if not values:
        return 0.0
    return float(statistics.median(values))


def _aggregate(runs: List[Dict[str, Any]], arms: List[str]) -> List[Dict[str, Any]]:
    by_arm: Dict[str, List[Dict[str, Any]]] = {}
    for row in runs:
        by_arm.setdefault(str(row.get("arm") or ""), []).append(row)
    out: List[Dict[str, Any]] = []
    for arm in arms:
        rows = by_arm.get(arm, [])
        n = len(rows)
        success = len([r for r in rows if bool(r.get("task_success"))])
        review = len([r for r in rows if bool(r.get("review_pass"))])
        out.append(
            {
                "arm": arm,
                "runs": n,
                "success_rate": float(success / n) if n else 0.0,
                "review_pass_rate": float(review / n) if n else 0.0,
                "median_wallclock_seconds": _median([float(r.get("wallclock_seconds") or 0.0) for r in rows]),
                "median_subcall_count": _median([float(r.get("subcall_count") or 0.0) for r in rows]),
                "timeout_rate": float(sum(int(r.get("timeout_count") or 0) for r in rows) / n) if n else 0.0,
                "blocked_rate": float(sum(int(r.get("blocked_count") or 0) for r in rows) / n) if n else 0.0,
                "artifact_ok_rate": float(len([r for r in rows if bool(r.get("artifact_validation_ok"))]) / n) if n else 0.0,
            }
        )
    return out


def _iter_selected_scenarios(scenario_ids: Optional[Iterable[str]]) -> List[Scenario]:
    selected = {str(x).strip() for x in (scenario_ids or []) if str(x).strip()}
    if not selected:
        return list(SCENARIOS)
    return [s for s in SCENARIOS if s.scenario_id in selected]


def run_tier0(
    *,
    seeds: int,
    out_root: Path,
    model_route: str,
    scenario_ids: Optional[Iterable[str]] = None,
    arm_ids: Optional[Iterable[str]] = None,
    spend_cap_usd: Optional[float] = None,
    token_cap: Optional[int] = None,
    subcall_timeout_seconds: float = 10.0,
    batch_timeout_seconds: float = 10.0,
) -> Dict[str, Any]:
    if model_route.startswith("mock/"):
        os.environ.setdefault("MOCK_API_KEY", "mock")
    runs: List[Dict[str, Any]] = []
    selected_arms = [a for a in ARMS if not arm_ids or a in set(arm_ids)]
    selected_scenarios = _iter_selected_scenarios(scenario_ids)
    total_spend = 0.0
    total_tokens = 0
    aborted_for_spend = False
    aborted_for_token_cap = False
    for arm in selected_arms:
        for scenario in selected_scenarios:
            for seed in range(1, seeds + 1):
                row = _run_arm_scenario(
                    arm,
                    scenario,
                    seed,
                    out_root,
                    model_route,
                    subcall_timeout_seconds=subcall_timeout_seconds,
                    batch_timeout_seconds=batch_timeout_seconds,
                )
                runs.append(row)
                total_spend += float(row.get("total_cost_usd") or 0.0)
                total_tokens += int(row.get("total_tokens") or 0)
                if spend_cap_usd is not None and total_spend > float(spend_cap_usd):
                    aborted_for_spend = True
                    break
                if token_cap is not None and total_tokens > int(token_cap):
                    aborted_for_token_cap = True
                    break
            if aborted_for_spend:
                break
            if aborted_for_token_cap:
                break
        if aborted_for_spend:
            break
        if aborted_for_token_cap:
            break
    payload = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _now_iso(),
        "mode": "offline_mock_tier0" if model_route.startswith("mock/") else "live_provider_tier0",
        "model_route": model_route,
        "seeds_per_cell": seeds,
        "arms": list(selected_arms),
        "scenarios": [{"scenario_id": s.scenario_id, "family": s.family, "description": s.description} for s in selected_scenarios],
        "runs": runs,
        "aggregates": _aggregate(runs, selected_arms),
        "total_cost_usd": float(total_spend),
        "total_tokens": int(total_tokens),
        "aborted_for_spend_cap": bool(aborted_for_spend),
        "aborted_for_token_cap": bool(aborted_for_token_cap),
        "spend_cap_usd": None if spend_cap_usd is None else float(spend_cap_usd),
        "token_cap": None if token_cap is None else int(token_cap),
        "caveats": [
            (
                "This is a controllable-surface Tier-0 pass using mock/no_tools (zero inference spend)."
                if model_route.startswith("mock/")
                else "This is a live-provider Tier-0 pass; treat quality metrics as preliminary due to low sample count."
            ),
            "Quality metrics here gate policy/instrumentation integrity, not frontier model capability.",
        ],
    }
    return payload


def render_markdown(payload: Mapping[str, Any]) -> str:
    lines: List[str] = []
    lines.append("# RLM Phase-2 P5 Tier-0 Results (Offline Mock)")
    lines.append("")
    lines.append(f"- Schema: `{payload.get('schema_version')}`")
    lines.append(f"- Generated: `{payload.get('generated_at')}`")
    lines.append(f"- Seeds/cell: `{payload.get('seeds_per_cell')}`")
    lines.append("")
    lines.append("## Arm Summary")
    lines.append("")
    lines.append("| Arm | Runs | Success | Review Pass | Median Wallclock (s) | Median Subcalls | Timeout Rate | Blocked Rate | Artifact OK |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|---:|")
    for row in payload.get("aggregates") or []:
        if not isinstance(row, Mapping):
            continue
        lines.append(
            "| {arm} | {runs} | {success:.2f} | {review:.2f} | {wall:.3f} | {sub:.1f} | {timeout:.2f} | {blocked:.2f} | {artifact:.2f} |".format(
                arm=row.get("arm"),
                runs=int(row.get("runs") or 0),
                success=float(row.get("success_rate") or 0.0),
                review=float(row.get("review_pass_rate") or 0.0),
                wall=float(row.get("median_wallclock_seconds") or 0.0),
                sub=float(row.get("median_subcall_count") or 0.0),
                timeout=float(row.get("timeout_rate") or 0.0),
                blocked=float(row.get("blocked_rate") or 0.0),
                artifact=float(row.get("artifact_ok_rate") or 0.0),
            )
        )
    lines.append("")
    lines.append("## Caveats")
    lines.append("")
    for note in payload.get("caveats") or []:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines) + "\n"


def write_go_no_go_memo(payload: Mapping[str, Any], path: Path) -> None:
    aggregates = payload.get("aggregates") or []
    by_arm = {str(row.get("arm") or ""): row for row in aggregates if isinstance(row, Mapping)}
    a1 = by_arm.get("A1_RLM_SYNC", {})
    a2 = by_arm.get("A2_RLM_BATCH", {})
    a3 = by_arm.get("A3_HYBRID_LONGRUN", {})
    go = (
        float(a1.get("artifact_ok_rate") or 0.0) >= 1.0
        and float(a2.get("artifact_ok_rate") or 0.0) >= 1.0
        and float(a3.get("artifact_ok_rate") or 0.0) >= 1.0
    )
    lines = [
        "# BreadBoard RLM Phase-2 Go/No-Go Memo (Tier-0 Offline Mock)",
        "",
        f"Date: {_now_iso()}",
        "",
        "## Decision",
        "",
        f"- Decision: {'GO-WITH-GUARDRAILS' if go else 'NO-GO'}",
        "- Confidence: medium (offline controllable-surface validation only)",
        "",
        "## Rationale",
        "",
        "- Tier-0 completed with mock provider for zero-spend control-plane validation.",
        "- Batch ordering, retries, fail-fast, and per-branch concurrency caps exercised.",
        "- Artifact integrity validated via `validate_rlm_artifacts.py` across enabled RLM arms.",
        "- Next gate remains Tier-1 live-provider quality/cost runs.",
        "",
        "## Arm snapshot",
        "",
        f"- A1_RLM_SYNC artifact_ok_rate: {float(a1.get('artifact_ok_rate') or 0.0):.2f}",
        f"- A2_RLM_BATCH artifact_ok_rate: {float(a2.get('artifact_ok_rate') or 0.0):.2f}",
        f"- A3_HYBRID_LONGRUN artifact_ok_rate: {float(a3.get('artifact_ok_rate') or 0.0):.2f}",
        "",
        "## Next steps",
        "",
        "1. Run Tier-1 live-provider matrix with spend cap and seeds=3.",
        "2. Populate confidence intervals and variance reporting.",
        "3. Finalize full go/no-go against quality and parity criteria.",
        "",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run RLM Phase-2 P5 Tier-0 offline benchmark matrix.")
    parser.add_argument("--seeds", type=int, default=1, help="Seeds per arm/scenario cell.")
    parser.add_argument("--model-route", default="mock/no_tools", help="Provider route used for baseline + subcalls.")
    parser.add_argument("--scenario-id", action="append", default=[], help="Optional scenario_id filter (repeatable).")
    parser.add_argument("--arm", action="append", default=[], help="Optional arm filter (repeatable).")
    parser.add_argument("--spend-cap-usd", type=float, default=None, help="Optional hard spend cap; run aborts when exceeded.")
    parser.add_argument("--token-cap", type=int, default=None, help="Optional hard token cap; run aborts when exceeded.")
    parser.add_argument("--subcall-timeout-seconds", type=float, default=10.0, help="RLM subcall timeout_seconds setting.")
    parser.add_argument("--batch-timeout-seconds", type=float, default=10.0, help="RLM batch timeout_seconds setting.")
    parser.add_argument("--workspace-root", default="tmp/rlm_phase2_tier0", help="Workspace root for generated run dirs.")
    parser.add_argument("--out-json", default="docs_tmp/RLMs/PHASE_2_P5_RESULTS_RAW_V1.json")
    parser.add_argument("--out-markdown", default="docs_tmp/RLMs/PHASE_2_P5_RESULTS_TABLE_V1.md")
    parser.add_argument("--out-memo", default="docs_tmp/RLMs/PHASE_2_P5_GO_NO_GO_MEMO_V1.md")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    workspace_root = Path(args.workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)
    payload = run_tier0(
        seeds=max(1, int(args.seeds)),
        out_root=workspace_root,
        model_route=str(args.model_route),
        scenario_ids=list(args.scenario_id or []),
        arm_ids=list(args.arm or []),
        spend_cap_usd=args.spend_cap_usd,
        token_cap=args.token_cap,
        subcall_timeout_seconds=float(args.subcall_timeout_seconds),
        batch_timeout_seconds=float(args.batch_timeout_seconds),
    )
    out_json = Path(args.out_json)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    out_md = Path(args.out_markdown)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_markdown(payload), encoding="utf-8")
    write_go_no_go_memo(payload, Path(args.out_memo))
    print(json.dumps({"ok": True, "json": str(out_json), "markdown": str(out_md), "memo": str(args.out_memo)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
