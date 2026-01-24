#!/usr/bin/env python3
"""Run real E2E scenarios via main.py and bundle results."""

from __future__ import annotations

import argparse
import copy
import json
import os
import shutil
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

try:
    import yaml  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    yaml = None

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _now_slug() -> str:
    return time.strftime("%Y%m%d-%H%M%S", time.gmtime())


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _require(obj: Dict[str, Any], key: str) -> Any:
    if key not in obj:
        raise ValueError(f"missing required field: {key}")
    return obj[key]


def _validate_scenario(payload: Dict[str, Any]) -> None:
    _require(payload, "schema_version")
    _require(payload, "id")
    prompt = _require(payload, "prompt")
    if not isinstance(prompt, dict) or not isinstance(prompt.get("user"), str):
        raise ValueError("prompt.user is required")
    conditions = _require(payload, "ctrees_conditions")
    if not isinstance(conditions, list) or not conditions:
        raise ValueError("ctrees_conditions must be a non-empty list")


def _load_yaml(path: Path) -> Dict[str, Any]:
    if yaml is None:
        raise RuntimeError("PyYAML is required to load config YAML files.")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Config YAML must be an object: {path}")
    return data


def _write_yaml(path: Path, payload: Dict[str, Any]) -> None:
    if yaml is None:
        raise RuntimeError("PyYAML is required to write config YAML files.")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=False),
        encoding="utf-8",
    )


def _write_task_file(text: str) -> Path:
    tmp = tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8", suffix=".txt")
    tmp.write(text)
    tmp.flush()
    return Path(tmp.name)

def _run_main(
    config_path: Path,
    task_path: Path,
    workspace: Path,
    result_json: Path,
    *,
    env: Optional[Dict[str, str]] = None,
    max_iterations: Optional[int] = None,
) -> int:
    cmd = [
        "python",
        str(_REPO_ROOT / "main.py"),
        str(config_path),
        "--task",
        str(task_path),
        "--workspace",
        str(workspace),
        "--result-json",
        str(result_json),
    ]
    if isinstance(max_iterations, int) and max_iterations > 0:
        cmd.extend(["--max-iterations", str(max_iterations)])
    return subprocess.run(cmd, cwd=str(_REPO_ROOT), env=env).returncode


def _bundle_run(log_dir: Path, out_dir: Path) -> None:
    cmd = ["python", str(_REPO_ROOT / "scripts" / "ctrees_lab_bundle.py"), str(log_dir), "--out", str(out_dir), "--overwrite"]
    subprocess.run(cmd, cwd=str(_REPO_ROOT), check=False)


def _qc_bundle(bundle_dir: Path, *, require_metrics: bool) -> bool:
    env = os.environ.copy()
    if require_metrics:
        env["BREADBOARD_QC_REQUIRE_METRICS"] = "1"
    cmd = ["python", str(_REPO_ROOT / "scripts" / "ctlab.py"), "qc", str(bundle_dir)]
    return subprocess.run(cmd, cwd=str(_REPO_ROOT), env=env).returncode == 0


def _classify_assistant_text(text: str) -> str:
    text = text.strip()
    if text.startswith("<TOOL_CALL>") and text.endswith("</TOOL_CALL>"):
        return "tool_call"
    if text.lower().startswith("tool execution results:"):
        return "tool_result"
    return "assistant"


def _last_assistant_text_info(run_dir: Path) -> Dict[str, Any]:
    conv = run_dir / "meta" / "conversation_ir.json"
    if not conv.exists():
        return {"text": None, "kind": None, "non_tool_seen": False}
    payload = _load_json(conv)
    messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
    last_text = None
    last_kind = None
    non_tool_seen = False
    for entry in messages:
        if not isinstance(entry, dict) or entry.get("role") != "assistant":
            continue
        parts = entry.get("parts") if isinstance(entry.get("parts"), list) else []
        for part in parts:
            if isinstance(part, dict) and isinstance(part.get("text"), str):
                text = part.get("text").strip()
                if not text:
                    continue
                kind = _classify_assistant_text(text)
                if kind == "assistant":
                    non_tool_seen = True
                if kind != "tool_call":
                    last_text = text
                    last_kind = kind
    return {"text": last_text, "kind": last_kind, "non_tool_seen": non_tool_seen}


def _score(text: Optional[str], scoring: Dict[str, Any], *, workspace: Optional[Path] = None) -> Dict[str, Any]:
    if not text:
        return {"ok": False, "reason": "missing_text"}
    checks = scoring.get("checks") if isinstance(scoring.get("checks"), list) else []
    for check in checks:
        if not isinstance(check, dict):
            continue
        ctype = check.get("type")
        if ctype == "regex_match":
            import re

            pattern = check.get("pattern")
            if not isinstance(pattern, str):
                return {"ok": False, "reason": "invalid_pattern"}
            if re.search(pattern, text, flags=re.IGNORECASE) is None:
                return {"ok": False, "reason": "regex_no_match"}
        if ctype == "contains_text":
            expected = check.get("expected")
            if not isinstance(expected, str):
                return {"ok": False, "reason": "invalid_expected"}
            if expected.lower() not in text.lower():
                return {"ok": False, "reason": "missing_expected"}
        if ctype == "json_exact":
            expected = check.get("expected")
            if expected is None:
                return {"ok": False, "reason": "missing_expected"}
            try:
                parsed = json.loads(text)
            except Exception:
                return {"ok": False, "reason": "invalid_json"}
            if parsed != expected:
                return {"ok": False, "reason": "json_mismatch"}
        if ctype == "command_exitcode":
            command = check.get("command")
            expected_code = int(check.get("expect", 0))
            workdir = check.get("workdir")
            if not isinstance(command, str):
                return {"ok": False, "reason": "invalid_command"}
            cwd = None
            if isinstance(workdir, str):
                workdir_path = Path(workdir)
                if not workdir_path.is_absolute() and workspace is not None:
                    workdir_path = workspace / workdir_path
                cwd = str(workdir_path)
            result = subprocess.run(command, shell=True, cwd=cwd)
            if result.returncode != expected_code:
                return {"ok": False, "reason": f"exitcode_{result.returncode}"}
    return {"ok": True, "reason": None}


def _extract_max_turns(stop_conditions: Any) -> Optional[int]:
    if not isinstance(stop_conditions, list):
        return None
    for entry in stop_conditions:
        if not isinstance(entry, dict):
            continue
        if entry.get("type") == "max_turns":
            value = entry.get("value")
            try:
                value = int(value)
            except Exception:
                continue
            if value > 0:
                return value
    return None


def _apply_fixture(fixture: Optional[Any], workspace: Path) -> None:
    if not fixture:
        return
    fixture_path = Path(fixture)
    if not fixture_path.is_absolute():
        fixture_path = _REPO_ROOT / fixture_path
    if fixture_path.is_dir():
        shutil.copytree(fixture_path, workspace, dirs_exist_ok=True)
        return
    if fixture_path.is_file() and fixture_path.suffix == ".zip":
        with zipfile.ZipFile(fixture_path) as zf:
            zf.extractall(workspace)


def _apply_ctrees_condition(base_cfg: Dict[str, Any], condition_ctrees: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(condition_ctrees, dict):
        return base_cfg
    cfg = copy.deepcopy(base_cfg)
    ctrees_cfg = dict(cfg.get("ctrees") or {})
    engine_cfg = dict(ctrees_cfg.get("context_engine") or {})
    if "context_engine" in condition_ctrees and isinstance(condition_ctrees.get("context_engine"), dict):
        engine_cfg.update(condition_ctrees["context_engine"])
        for key, value in condition_ctrees.items():
            if key == "context_engine":
                continue
            ctrees_cfg[key] = value
    else:
        engine_cfg.update(condition_ctrees)
    ctrees_cfg["context_engine"] = engine_cfg
    cfg["ctrees"] = ctrees_cfg
    return cfg


def _ensure_fixture_persistence(cfg: Dict[str, Any], *, fixture_present: bool) -> Dict[str, Any]:
    if not fixture_present:
        return cfg
    limits_cfg = dict(cfg.get("limits") or {})
    if limits_cfg.get("clean_workspace") is True:
        limits_cfg["clean_workspace"] = False
        cfg["limits"] = limits_cfg
    return cfg


def _apply_tool_policies(base_cfg: Dict[str, Any], tools_cfg: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not isinstance(tools_cfg, dict):
        return base_cfg
    cfg = copy.deepcopy(base_cfg)
    policies = dict(cfg.get("policies") or {})
    tools_policy = dict(policies.get("tools") or {})
    allowlist = tools_cfg.get("allowlist")
    forbidden = tools_cfg.get("forbidden")
    if isinstance(allowlist, list):
        tools_policy["allow"] = [str(item) for item in allowlist if str(item).strip()]
    if isinstance(forbidden, list):
        tools_policy["deny"] = [str(item) for item in forbidden if str(item).strip()]
    if tools_policy:
        policies["tools"] = tools_policy
    if isinstance(forbidden, list) and any(str(item).lower() == "network" for item in forbidden):
        network_policy = dict(policies.get("network") or {})
        network_policy.setdefault("deny", ["*"])
        policies["network"] = network_policy
    if policies:
        cfg["policies"] = policies
    return cfg


def _ensure_default_model(cfg: Dict[str, Any]) -> Dict[str, Any]:
    model_cfg = cfg.get("model")
    if isinstance(model_cfg, dict):
        model_id = model_cfg.get("id") or model_cfg.get("model")
        provider = model_cfg.get("provider")
        if isinstance(model_id, str) and model_id.strip():
            model_id = model_id.strip()
            if isinstance(provider, str) and provider.strip() and "/" not in model_id:
                model_id = f"{provider.strip()}/{model_id}"
            providers_cfg = dict(cfg.get("providers") or {})
            providers_cfg.setdefault("default_model", model_id)
            cfg["providers"] = providers_cfg
    return cfg


def _extract_tool_calls(run_summary: Optional[Dict[str, Any]]) -> List[str]:
    if not isinstance(run_summary, dict):
        return []
    names: List[str] = []
    turn_usage = run_summary.get("turn_tool_usage")
    if isinstance(turn_usage, dict):
        for entry in turn_usage.values():
            if not isinstance(entry, dict):
                continue
            tools = entry.get("tools")
            if not isinstance(tools, list):
                continue
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                name = tool.get("name")
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
        if names:
            return names
    diagnostics = run_summary.get("turn_diagnostics")
    if isinstance(diagnostics, list):
        for entry in diagnostics:
            if not isinstance(entry, dict):
                continue
            tool_calls = entry.get("tool_calls") if isinstance(entry.get("tool_calls"), list) else []
            for call in tool_calls:
                if not isinstance(call, dict):
                    continue
                name = call.get("name")
                if isinstance(name, str) and name.strip():
                    names.append(name.strip())
    return names


def _evaluate_tool_requirements(
    tool_calls: List[str],
    tools_cfg: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not isinstance(tools_cfg, dict):
        return {"ok": True, "reason": None}
    required = tools_cfg.get("required") if isinstance(tools_cfg.get("required"), list) else []
    allowlist = tools_cfg.get("allowlist") if isinstance(tools_cfg.get("allowlist"), list) else []
    forbidden = tools_cfg.get("forbidden") if isinstance(tools_cfg.get("forbidden"), list) else []
    ignore = {"mark_task_complete"}
    called = [name.lower() for name in tool_calls if name.lower() not in ignore]
    if required:
        for req in required:
            req_name = str(req).lower()
            if req_name and req_name not in called:
                return {"ok": False, "reason": f"missing_required:{req_name}"}
    if allowlist:
        allowed = {str(item).lower() for item in allowlist}
        for name in called:
            if name not in allowed:
                return {"ok": False, "reason": f"disallowed_tool:{name}"}
    if forbidden:
        forbidden_set = {str(item).lower() for item in forbidden}
        for name in called:
            if name in forbidden_set:
                return {"ok": False, "reason": f"forbidden_tool:{name}"}
    return {"ok": True, "reason": None}


def _load_run_summary(bundle_dir: Path) -> Optional[Dict[str, Any]]:
    path = bundle_dir / "meta" / "run_summary.json"
    if not path.exists():
        return None
    return _load_json(path)


def _normalize_model_name(name: str) -> str:
    if "/" in name:
        return name.split("/")[-1]
    return name


def _load_pricing_snapshot(run_summary: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    explicit = None
    if isinstance(run_summary, dict):
        explicit = run_summary.get("pricing_snapshot")
    if isinstance(explicit, str):
        candidate = Path(explicit)
        if not candidate.is_absolute():
            candidate = _REPO_ROOT / candidate
        if candidate.exists():
            return _load_json(candidate)
    default_path = _REPO_ROOT / "experiments/ctrees/pricing/openai_api_pricing.json"
    if default_path.exists():
        return _load_json(default_path)
    return None


def _extract_usage_summary(run_summary: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not isinstance(run_summary, dict):
        return None
    usage = run_summary.get("usage_summary")
    if isinstance(usage, dict):
        return usage
    return None


def _compute_usd_estimate(
    usage_summary: Optional[Dict[str, Any]],
    pricing: Optional[Dict[str, Any]],
) -> Optional[float]:
    if not isinstance(usage_summary, dict) or not isinstance(pricing, dict):
        return None
    models = usage_summary.get("by_model")
    if not isinstance(models, dict):
        return None
    prices = pricing.get("models") if isinstance(pricing.get("models"), dict) else {}
    if not prices:
        return None
    total = 0.0
    for model_name, usage in models.items():
        if not isinstance(model_name, str) or not isinstance(usage, dict):
            continue
        normalized = _normalize_model_name(model_name)
        price = prices.get(normalized)
        if not isinstance(price, dict):
            continue
        input_tokens = float(usage.get("input_tokens") or 0)
        output_tokens = float(usage.get("output_tokens") or 0)
        cached_tokens = float(usage.get("cached_input_tokens") or 0)
        input_rate = float(price.get("input") or 0)
        output_rate = float(price.get("output") or 0)
        cached_rate = float(price.get("cached_input") or 0)
        total += (input_tokens / 1_000_000.0) * input_rate
        total += (output_tokens / 1_000_000.0) * output_rate
        if cached_tokens and cached_rate:
            total += (cached_tokens / 1_000_000.0) * cached_rate
    return total


def _extract_total_time_ms(run_summary: Optional[Dict[str, Any]]) -> Optional[float]:
    if not isinstance(run_summary, dict):
        return None
    for key in ("total_time_ms", "duration_ms"):
        value = run_summary.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    completion = run_summary.get("completion_summary")
    if isinstance(completion, dict):
        value = completion.get("total_time_ms")
        if isinstance(value, (int, float)):
            return float(value)
    return None


def _load_metrics_turns(metrics_path: Path) -> List[Dict[str, Any]]:
    if not metrics_path.exists():
        return []
    rows: List[Dict[str, Any]] = []
    for line in metrics_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def _latency_avg(metrics_path: Path) -> Optional[float]:
    rows = _load_metrics_turns(metrics_path)
    vals: List[float] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        latency = row.get("latency_seconds")
        if isinstance(latency, (int, float)):
            vals.append(float(latency))
    if not vals:
        return None
    return sum(vals) / len(vals)


def _expand_ablation(conditions: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    expanded: List[Dict[str, Any]] = []
    seen_ids: set[str] = set()
    for condition in conditions:
        if not isinstance(condition, dict):
            continue
        base_id = str(condition.get("id") or "condition")
        if base_id not in seen_ids:
            expanded.append(condition)
            seen_ids.add(base_id)
        ctrees_cfg = condition.get("ctrees")
        if not isinstance(ctrees_cfg, dict):
            continue
        enabled = ctrees_cfg.get("enabled")
        if enabled is True:
            ablation = copy.deepcopy(condition)
            ablation["id"] = f"{base_id}.ablation.no_ctrees"
            ablation_ctrees = dict(ctrees_cfg)
            ablation_ctrees["enabled"] = False
            ablation_ctrees.setdefault("mode", "off")
            ablation["ctrees"] = ablation_ctrees
            if ablation["id"] not in seen_ids:
                expanded.append(ablation)
                seen_ids.add(ablation["id"])
        if enabled is False:
            ablation = copy.deepcopy(condition)
            ablation["id"] = f"{base_id}.ablation.ctrees"
            ablation_ctrees = dict(ctrees_cfg)
            ablation_ctrees["enabled"] = True
            ablation["ctrees"] = ablation_ctrees
            if ablation["id"] not in seen_ids:
                expanded.append(ablation)
                seen_ids.add(ablation["id"])
    return expanded


def main() -> int:
    parser = argparse.ArgumentParser(description="Run E2E scenario via main.py and bundle results.")
    parser.add_argument("scenario", help="Path to scenario JSON")
    parser.add_argument("--config", help="Default agent config YAML (used if condition has no config_path)")
    parser.add_argument("--out", default="logs/ctrees_e2e_runs", help="Output root for runs")
    parser.add_argument("--conditions", nargs="+", help="Condition ids to run (space-separated).")
    parser.add_argument("--repeat", type=int, help="Repeat count per condition (defaults to scenario.repetitions.dev).")
    parser.add_argument("--ablation", action="store_true", help="Run ablation variants for each condition.")
    parser.add_argument(
        "--allow-missing-metrics",
        action="store_true",
        help="Do not require metrics/turns.jsonl for QC gating.",
    )
    parser.add_argument(
        "--require-final-assistant-text",
        action="store_true",
        help="Fail scoring if the last assistant message is not user-facing text.",
    )
    args = parser.parse_args()

    scenario_path = Path(args.scenario)
    if not scenario_path.is_absolute():
        scenario_path = _REPO_ROOT / scenario_path
    scenario = _load_json(scenario_path)
    _validate_scenario(scenario)

    out_root = Path(args.out)
    if not out_root.is_absolute():
        out_root = _REPO_ROOT / out_root
    out_root.mkdir(parents=True, exist_ok=True)

    prompt = scenario.get("prompt", {})
    user_text = prompt.get("user") if isinstance(prompt, dict) else None
    if not isinstance(user_text, str):
        raise ValueError("prompt.user must be a string")

    task_file = _write_task_file(user_text)
    scoring = scenario.get("scoring") if isinstance(scenario.get("scoring"), dict) else {}
    tools_cfg = scenario.get("tools") if isinstance(scenario.get("tools"), dict) else None
    setup = scenario.get("setup") if isinstance(scenario.get("setup"), dict) else {}
    env_overrides = setup.get("env") if isinstance(setup.get("env"), dict) else {}
    fixture = setup.get("fixture")
    max_turns = _extract_max_turns(scenario.get("stop_conditions"))
    repetitions = scenario.get("repetitions") if isinstance(scenario.get("repetitions"), dict) else {}
    repeat_count = args.repeat if args.repeat is not None else int(repetitions.get("dev") or 1)
    repeat_count = max(1, repeat_count)
    require_metrics = not args.allow_missing_metrics

    results: List[Dict[str, Any]] = []
    condition_filter = {str(cid) for cid in args.conditions} if args.conditions else None
    raw_conditions = [c for c in scenario.get("ctrees_conditions", []) if isinstance(c, dict)]
    conditions = _expand_ablation(raw_conditions) if args.ablation else raw_conditions
    for condition in conditions:
        if not isinstance(condition, dict):
            continue
        condition_id = condition.get("id") or "condition"
        if condition_filter and str(condition_id) not in condition_filter:
            continue
        config_path = condition.get("config_path")
        if not config_path:
            if not args.config:
                raise ValueError("missing config_path for condition and --config not provided")
            config_path = args.config
        config_path = Path(config_path)
        if not config_path.is_absolute():
            config_path = _REPO_ROOT / config_path

        base_cfg = _load_yaml(config_path)
        condition_ctrees = condition.get("ctrees") if isinstance(condition.get("ctrees"), dict) else None
        effective_cfg = _apply_ctrees_condition(base_cfg, condition_ctrees)
        effective_cfg = _apply_tool_policies(effective_cfg, tools_cfg)
        effective_cfg = _ensure_default_model(effective_cfg)
        effective_cfg = _ensure_fixture_persistence(effective_cfg, fixture_present=bool(fixture))

        for rep in range(1, repeat_count + 1):
            run_dir = out_root / f"{scenario.get('id')}-{condition_id}-{_now_slug()}-r{rep}"
            workspace = run_dir / "workspace"
            workspace.mkdir(parents=True, exist_ok=True)
            _apply_fixture(fixture, workspace)
            result_json = run_dir / "result.json"
            cfg_path = run_dir / "config.yaml"
            _write_yaml(cfg_path, effective_cfg)
            run_env = os.environ.copy()
            for key, value in env_overrides.items():
                run_env[str(key)] = str(value)
            if not run_env.get("BREADBOARD_PRICING_SNAPSHOT"):
                pricing_path = _REPO_ROOT / "experiments" / "ctrees" / "pricing" / "openai_api_pricing.json"
                if pricing_path.exists():
                    run_env["BREADBOARD_PRICING_SNAPSHOT"] = str(pricing_path)
            if fixture:
                run_env["PRESERVE_SEEDED_WORKSPACE"] = "1"
            started_at = _now_iso()
            code = _run_main(
                cfg_path,
                task_file,
                workspace,
                result_json,
                env=run_env,
                max_iterations=max_turns,
            )
            completed_at = _now_iso()
            payload = _load_json(result_json) if result_json.exists() else {}
            run_info = payload.get("result") if isinstance(payload.get("result"), dict) else {}
            log_dir = run_info.get("run_dir") or run_info.get("logging_dir")
            bundle_dir = run_dir / "ctrees_lab_bundle"
            if log_dir:
                _bundle_run(Path(log_dir), bundle_dir)
            qc_ok = _qc_bundle(bundle_dir, require_metrics=require_metrics) if bundle_dir.exists() else False
            final_info = _last_assistant_text_info(Path(log_dir)) if log_dir else {"text": None, "kind": None, "non_tool_seen": False}
            final_text = final_info.get("text")
            score = _score(final_text, scoring, workspace=workspace) if scoring else {"ok": True, "reason": None}
            if args.require_final_assistant_text and final_info.get("kind") != "assistant":
                score = {"ok": False, "reason": "final_assistant_not_text"}
            run_summary = _load_run_summary(bundle_dir) if bundle_dir.exists() else None
            tool_calls = _extract_tool_calls(run_summary)
            tool_requirements = _evaluate_tool_requirements(tool_calls, tools_cfg)
            usage_summary = _extract_usage_summary(run_summary)
            pricing_snapshot = _load_pricing_snapshot(run_summary)
            usd_estimate = _compute_usd_estimate(usage_summary, pricing_snapshot)
            total_time_ms = _extract_total_time_ms(run_summary)
            metrics_turns = bundle_dir / "metrics" / "turns.jsonl"
            latency_avg = _latency_avg(metrics_turns) if bundle_dir.exists() else None
            metrics_ok = metrics_turns.exists() if bundle_dir.exists() else False

            results.append(
                {
                    "scenario_id": scenario.get("id"),
                    "condition_id": condition_id,
                    "repeat_index": rep,
                    "run_dir": str(run_dir),
                    "log_dir": str(log_dir) if log_dir else None,
                    "bundle_dir": str(bundle_dir) if bundle_dir.exists() else None,
                    "qc_ok": qc_ok,
                    "metrics_ok": metrics_ok,
                    "score": score,
                    "tool_requirements": tool_requirements,
                    "exit_code": code,
                    "final_assistant_kind": final_info.get("kind"),
                    "usage_summary": usage_summary,
                    "usd_estimate": usd_estimate,
                    "total_time_ms": total_time_ms,
                    "latency_avg_seconds": latency_avg,
                    "condition_ctrees": condition_ctrees,
                    "run_started_at": started_at,
                    "run_completed_at": completed_at,
                }
            )

    out_path = out_root / f"{scenario.get('id')}_results.json"
    out_path.write_text(json.dumps({"results": results}, indent=2), encoding="utf-8")
    print(f"[ctlab] wrote {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
