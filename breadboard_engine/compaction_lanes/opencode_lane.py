"""opencode lane responsibility owner."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import shutil
from contextlib import nullcontext
from typing import Any
from .opencode_comparison import CreatedTimeTieOrder, RuntimeGeneratedIDs, canonical, compare_records, is_summary, stock_control_flow_ties
from .opencode_episode import run_bb
from .opencode_mcp import MCPRelay
from .opencode_recording import ResponsesProvider
from .opencode_scenarios import PLUGIN_COMMIT, PLUGIN_EVIDENCE, PLUGIN_SCENARIOS, PROFILES, ROOT, SCENARIOS, STOCK_VERSION, production_settings, responses_sse, scenario_turns, text_response
from .opencode_stock import create_empty_stock_sessions, run_stock
from .request_bytes import guarded_changes, raw_request_differences, request_json_problem

def auxiliary_script(scenario: str) -> list[dict[str, Any]]:
    if scenario == "delegate_prune":
        return [{"type": "delegated_pending"}]
    if scenario == "delegate_idle":
        return [{"type": "delegated_complete", "response": responses_sse(
            text_response("Delegated fixture inspection complete."), -1)}]
    return []

def archive_bb_session(workspace: Path, out: Path, session_id: str) -> str | None:
    """Retain a closed, lane-owned product/native store before a fresh attempt."""
    from breadboard.product.runtime.artifacts import is_portable_basename
    from breadboard.product.runtime.session_store import session_directory
    if not is_portable_basename(session_id):
        raise ValueError("BB oracle session ID is not a portable store key")
    source = session_directory(workspace) / session_id
    if not source.exists():
        return None
    if source.is_symlink():
        raise ValueError("BB oracle session store cannot be a symlink")
    destination = out / "bb_session_store"
    if destination.exists():
        raise FileExistsError(f"BB oracle archive already exists: {destination}")
    shutil.move(str(source), str(destination))
    return str(destination)

def capture_side(side, harness, scenario, workspace, out, provider, script, sessions,
                 relay, stock_session_id=None):
    out.mkdir(parents=True, exist_ok=True)
    (out / "source_seed_sessions.json").write_text(json.dumps(sessions, indent=2) + "\n")
    provider.engine.set_script(script)
    provider.engine.recorded_requests.clear()
    if scenario in {"delegate_prune", "delegate_idle"}:
        provider.engine.set_child_script(sessions[0]["info"]["id"], auxiliary_script(scenario))
    else:
        provider.engine.delegated_root = None
    result_path = out / f"{side}_result.json"
    if result_path.exists():
        raise FileExistsError(f"Use a fresh lane output directory: {result_path}")
    command = [sys.executable, "-m", "scripts.compaction_lanes.opencode_lane",
               "--child", side, "--harness", harness, "--scenario", scenario,
               "--out", str(out), "--workspace", str(workspace),
               "--base-url", provider.base_url + "/v1"]
    if relay is not None:
        command += ["--mcp-relay", relay.url]
    if side == "bb":
        command += ["--stock-session-id", str(stock_session_id or "")]
    process = subprocess.run(command, cwd=ROOT, env={**os.environ, "BB_WORKSPACE_ROOT": str(ROOT),
                             "PYTHONPATH": str(ROOT), "RAY_SCE_LOCAL_MODE": "1", "RAY_SCE_SKIP_LSP": "1"},
                             input="", capture_output=True, text=True, timeout=360)
    if scenario in {"delegate_prune", "delegate_idle"}:
        provider.engine.delegated_release.set()
        if provider.engine.delegated_arrived.is_set() and not provider.engine.delegated_finished.wait(10):
            raise RuntimeError("The lane failed to release its real background request")
    (out / f"{side}.stdout").write_text(process.stdout)
    (out / f"{side}.stderr").write_text(process.stderr)
    result = json.loads(result_path.read_bytes()) if result_path.is_file() else {
        "error": process.stderr, "returncode": process.returncode}
    observation_path = out / "bb_queued_observation.json"
    if side == "bb" and observation_path.is_file():
        result["queued_observation_artifact"] = str(observation_path)
    result["unconsumed_responses"] = len(provider.engine.script)
    result["unconsumed_auxiliary_responses"] = len(provider.engine.child_script or [])
    rows = list(provider.engine.recorded_requests)
    (out / f"{side}_requests.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
    if side == "bb":
        result["archived_session_store"] = archive_bb_session(workspace, out, stock_session_id)
        result_path.write_text(json.dumps(result, indent=2) + "\n")
    return result, rows

def run_scenario(harness: str, scenario: str, out: Path) -> dict[str, Any]:
    out = out.resolve()
    out.mkdir(parents=True, exist_ok=True)
    # A workspace outside the engine repo has no inherited project/git files.
    # Both real processes receive this identical source path and git state.
    workspace = Path(tempfile.mkdtemp(prefix="bb-opencode-lane-"))
    settings = production_settings(harness)
    seed_path = out / "source_seed_sessions.json"
    if scenario in PLUGIN_SCENARIOS and harness != "oh-my-opencode":
        raise ValueError("The plugin contributor and recovery scenarios require oh-my-opencode")
    sessions = create_empty_stock_sessions(workspace, out / "fixture_runtime", delegated=False)
    seed_path.write_text(json.dumps(sessions, indent=2) + "\n")
    descriptors = [response for turn in scenario_turns(scenario, settings)
                   for phase in ("responses", "autonomous_responses")
                   for response in turn.get(phase, [])]
    script = [responses_sse(response, i) for i, response in enumerate(descriptors)]
    records: dict[str, list[dict[str, Any]]] = {}
    sides: dict[str, Any] = {}
    # Each side gets a fresh stock/engine session and the identical scripted
    # provider. Same literal workspace path, no path-based comparator rewrites.
    with (MCPRelay() if harness == "oh-my-opencode" else nullcontext(None)) as relay, ResponsesProvider(script=script) as provider:
        attempts = []
        selected = None
        bb_runs = 0
        bb_attempts = []
        bb_selected = None
        for attempt in range(1, 6):
            attempt_out = out / "stock_attempts" / f"attempt-{attempt}"
            result, rows = capture_side("stock", harness, scenario, workspace, attempt_out,
                                        provider, script, sessions, relay)
            if not result.get("native_evidence", {}).get("messages"):
                result.setdefault("error", "Stock oracle omitted genuine native message evidence")
            ties = stock_control_flow_ties(result.get("native_evidence", {}))
            attempts.append({"attempt": attempt, "control_flow_ties": ties,
                             "requests": len(rows),
                             "compactions": sum(message["info"].get("summary") is True
                                                and message["info"].get("finish") == "stop"
                                                and not message["info"].get("error")
                                                for message in result.get("native_evidence", {}).get("messages", [])),
                             "error": result.get("error"),
                             "artifacts": {"result": str(attempt_out / "stock_result.json"),
                                           "requests": str(attempt_out / "stock_requests.jsonl")}})
            if not result.get("native_evidence", {}).get("messages"):
                break
            if ties:
                continue
            selected = attempt
            sides["stock"], records["stock"] = result, rows
            break
        if selected is None:
            sides["stock"], records["stock"] = result, rows
            if len(attempts) == 5 and all(item["control_flow_ties"] for item in attempts):
                sides["stock"]["error"] = "All five stock oracle attempts have native control-flow ties"
        (out / "stock_result.json").write_text(json.dumps(sides["stock"], indent=2) + "\n")
        (out / "stock_requests.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records["stock"]))
        if selected is not None and "error" not in sides["stock"]:
            for attempt in range(1, 6):
                attempt_out = out / "bb_attempts" / f"attempt-{attempt}"
                bb_runs += 1
                result, rows = capture_side(
                    "bb", harness, scenario, workspace, attempt_out, provider, script, sessions,
                    relay, sides["stock"]["session_id"])
                if not result.get("native_evidence", {}).get("messages"):
                    result.setdefault("error", "BB sidecar oracle omitted genuine native message evidence")
                ties = stock_control_flow_ties(result.get("native_evidence", {}))
                bb_attempts.append({
                    "attempt": attempt, "control_flow_ties": ties,
                    "requests": len(rows),
                    "compactions": sum(message["info"].get("summary") is True
                                       and message["info"].get("finish") == "stop"
                                       and not message["info"].get("error")
                                       for message in result.get("native_evidence", {}).get("messages", [])),
                    "error": result.get("error"),
                    "artifacts": {"result": str(attempt_out / "bb_result.json"),
                                  "requests": str(attempt_out / "bb_requests.jsonl"),
                                  "session_store": result.get("archived_session_store")},
                })
                sides["bb"], records["bb"] = result, rows
                if not result.get("native_evidence", {}).get("messages"):
                    break
                if ties:
                    continue
                bb_selected = attempt
                break
            if len(bb_attempts) == 5 and all(item["control_flow_ties"] for item in bb_attempts):
                sides["bb"]["error"] = "All five BB sidecar oracle attempts have native control-flow ties"
            observation = sides["bb"].get("queued_observation_artifact")
            if observation:
                shutil.copyfile(observation, out / "bb_queued_observation.json")
        else:
            sides["bb"], records["bb"] = {
                "error": "No successful tie-free stock oracle; BB not run",
                "unconsumed_responses": 0, "unconsumed_auxiliary_responses": 0}, []
        (out / "bb_result.json").write_text(json.dumps(sides["bb"], indent=2) + "\n")
        (out / "bb_requests.jsonl").write_text("".join(json.dumps(row) + "\n" for row in records["bb"]))
        relay_failures = list(relay.failures) if relay is not None else []
    deviations = []
    wire_problems = [problem for index, (stock, bb) in enumerate(zip(records["stock"], records["bb"]))
                     if (problem := request_json_problem(stock, bb, index)) is not None]
    guard_errors = list(wire_problems)
    compared = records
    if not wire_problems and scenario in {"delegate_prune", "delegate_idle"} and all("native_evidence" in sides[side] for side in ("stock", "bb")):
        try:
            guard = RuntimeGeneratedIDs(sides["stock"]["native_evidence"], sides["bb"]["native_evidence"],
                                        [item["info"]["id"] for item in sessions])
            compared = {side: [{**row, "json": guard.body(row["json"], side)} for row in rows]
                        for side, rows in records.items()}
            if guard.counts["stock"] != guard.counts["bb"]:
                raise ValueError("Runtime ID position substitution counts differ")
            deviations.append({"name": "opencode_runtime_generated_ids", "source": RuntimeGeneratedIDs.__doc__,
                               "mapping": guard.maps, "counts_per_position": guard.counts,
                               "completed_notification_provenance": guard.completed_notifications,
                               "provenance": {side: str(out / f"{side}_result.json") + "#native_evidence"
                                              for side in sides}})
        except ValueError as exc:
            compared = records
            guard_errors.append({"deviation_guard": str(exc)})
    tie_usage = []
    if not wire_problems and all("native_evidence" in sides[side] for side in ("stock", "bb")):
        tie_guard = CreatedTimeTieOrder(sides["stock"]["native_evidence"], sides["bb"]["native_evidence"])
        tied_rows = []
        for index, stock_row in enumerate(compared["stock"]):
            if index < len(compared["bb"]) and stock_row["path"] == compared["bb"][index]["path"]:
                body, groups = tie_guard.body(
                    stock_row["json"], compared["bb"][index]["json"],
                    raw_stock=records["stock"][index]["json"], raw_bb=records["bb"][index]["json"])
            else:
                body, groups = stock_row["json"], []
            tied_rows.append({**stock_row, "json": body})
            if groups:
                tie_usage.append({"request": index, "tie_groups": len(groups), "groups": groups})
        if tie_usage:
            compared = {**compared, "stock": tied_rows}
            deviations.append({"name": "opencode_created_time_tie_order",
                               "source": CreatedTimeTieOrder.__doc__, "counts_per_request": tie_usage,
                               "provenance": {side: str(out / f"{side}_result.json") + "#native_evidence.messages"
                                              for side in sides}})
    diffs = compare_records(compared["stock"], compared["bb"])
    unresolved = [{"side": side, "error": value["error"]} for side, value in sides.items() if "error" in value]
    unresolved.extend({"mcp_relay": error} for error in relay_failures)
    unresolved.extend(guard_errors)
    for side, result in sides.items():
        if result["unconsumed_responses"]:
            unresolved.append({"side": side, "unconsumed_responses": result["unconsumed_responses"]})
        if result["unconsumed_auxiliary_responses"]:
            unresolved.append({"side": side, "unconsumed_auxiliary_responses": result["unconsumed_auxiliary_responses"]})
    counts = {side: {
        "requests": len(rows),
        "compactions": sum(message["info"].get("summary") is True
                           and message["info"].get("finish") == "stop"
                           and not message["info"].get("error")
                           for message in sides[side].get("native_evidence", {}).get("messages", [])),
        "summary_requests": sum(is_summary(row) for row in rows),
        "requests_with_pruned_output": sum("[Old tool result content cleared]" in row["raw_body"] for row in rows),
    } for side, rows in records.items()}
    expected = 2 if scenario in {"auto_prune", "delegate_prune", "delegate_idle"} else 1
    if scenario == "overflow":
        expected += settings["deferred_recovery_attempts"]
    if scenario == "plugin_error":
        expected = settings["deferred_recovery_attempts"]
    scenario_findings = []
    if counts["stock"]["requests"] != counts["bb"]["requests"]:
        scenario_findings.append({"defect": "request counts differ", "stock": counts["stock"]["requests"], "bb": counts["bb"]["requests"]})
    for side, count in counts.items():
        if count["compactions"] != expected:
            scenario_findings.append({"side": side, "defect": "summary count", "expected": expected, "actual": count["compactions"]})
        if scenario in {"auto_prune", "delegate_prune"} and not count["requests_with_pruned_output"]:
            scenario_findings.append({"side": side, "defect": "pruning not visible in following requests"})
    prune_fences = {side: result.get("native_prune_fences", [])
                    for side, result in sides.items()}
    partial_overflow = scenario == "overflow" and settings["deferred_recovery_attempts"] > 0
    if partial_overflow:
        expected_calls = scenario_turns(scenario, settings)[2]["native_prune_call_ids"]
        for side, proofs in prune_fences.items():
            if (len(proofs) != 1
                    or proofs[0]["session_id"] != sides[side].get("session_id")
                    or proofs[0]["event_type"] != "message.part.updated"
                    or proofs[0]["call_ids"] != expected_calls
                    or set(proofs[0]["native_parts"]) != set(expected_calls)):
                scenario_findings.append({
                    "side": side, "defect": "Owned previous-turn native prune fence missing",
                    "native_prune_fences": proofs})
    scenario_findings.extend({"index": diff["index"], "defect": finding} for diff in diffs if diff["kind"] == "request_body" for finding in diff["compaction_input_findings"])
    scenario_findings.extend({"index": diff["index"], "defect": f"summary request field differs: {field['field']}"} for diff in diffs if diff["kind"] == "request_body" for field in diff["fields"] if field["classification"] == "compaction-path" and field["field"] != "input")
    if scenario in {"auto_prune", "delegate_prune"}:
        checkpoints = {side: [index for index, row in enumerate(rows) if any(item.get("type") == "function_call_output" and item.get("output") == "[Old tool result content cleared]" for item in row["json"].get("input", []))] for side, rows in records.items()}
        if checkpoints["stock"] != checkpoints["bb"]:
            scenario_findings.append({"defect": "pruning request checkpoints differ", **checkpoints})
    if scenario == "delegate_prune":
        for side, rows in records.items():
            summaries = [row for row in rows if is_summary(row)]
            children = sides[side].get("native_evidence", {}).get("children", [])
            child_ids = [child["session"]["id"] for child in children]
            if not summaries or not any(
                any(session_id in row["raw_body"] for session_id in child_ids)
                and "Active/Recent Delegated Sessions" in row["raw_body"] for row in summaries):
                scenario_findings.append({"side": side, "defect": "delegated session contributor absent from summary"})
    if scenario == "delegate_idle":
        followup = "\n" + scenario_turns(scenario, settings)[-1]["prompt"]
        for side in sides:
            messages = sides[side].get("native_evidence", {}).get("messages", [])
            notifications = [message for message in messages
                             if message["info"]["role"] == "user" and any(
                                 part.get("type") == "text"
                                 and RuntimeGeneratedIDs.COMPLETED_NOTIFICATION.fullmatch(part.get("text", ""))
                                 for part in message["parts"])]
            followups = [message for message in messages
                         if message["info"]["role"] == "user" and any(
                             part.get("type") == "text" and part.get("text") == followup
                             for part in message["parts"])]
            summaries = [message for message in messages
                         if message["info"].get("summary") is True
                         and message["info"].get("finish") == "stop"
                         and not message["info"].get("error")]
            if len(notifications) != 1 or len(followups) != 1 or len(summaries) != 2:
                scenario_findings.append({"side": side, "defect": "autonomous notification/followup/summary boundaries missing"})
            else:
                start = notifications[0]["info"]["time"]["created"]
                end = followups[0]["info"]["time"]["created"]
                if not all(start < summary["info"]["time"]["created"] < end for summary in summaries):
                    scenario_findings.append({"side": side, "defect": "compaction did not occur inside the autonomous source turn"})
                if side == "bb":
                    queued = sides[side].get("turns", [])
                    native_text = next(part["text"] for part in notifications[0]["parts"]
                                       if part.get("type") == "text")
                    if (len(queued) != 3 or any(turn["terminal_outcome"] != "completed" for turn in queued)
                            or queued[1]["content"] != native_text or queued[-1]["content"] != followup):
                        scenario_findings.append({"side": side, "defect": "production queue did not preserve the autonomous input and real followup"})
    if scenario == "plugin_error":
        for side in sides:
            errors = [message["info"].get("error", {}).get("name")
                      for message in sides[side].get("native_evidence", {}).get("messages", [])
                      if message["info"].get("error")]
            if errors != ["APIError"]:
                scenario_findings.append({"side": side, "defect": "plugin-only error is not the genuine native APIError",
                                          "native_errors": errors})
        reasons = [event["payload"]["payload"].get("reason")
                   for event in sides["bb"].get("events", [])
                   if event["type"] == "lifecycle_event"
                   and event["payload"].get("type") == "compaction_record_appended"
                   and event["payload"]["payload"].get("method") == "summary"]
        if reasons != ["threshold"] * expected:
            scenario_findings.append({"side": "bb", "defect": "plugin recovery entered native overflow or missed its checkpoint",
                                      "summary_reasons": reasons})
    raw_differences = raw_request_differences(records["stock"], records["bb"])
    deviation_records = []
    for difference in raw_differences:
        index = difference["index"]
        if index >= min(len(compared["stock"]), len(compared["bb"])):
            continue
        changes = {side: guarded_changes(records[side][index]["json"], compared[side][index]["json"])
                   for side in ("stock", "bb")}
        deviation_records.append({**difference, "names": [deviation["name"] for deviation in deviations],
                                  "authorities": [deviation["source"] for deviation in deviations],
                                  "changes": changes, "accepted": not any(diff.get("index") == index for diff in diffs)})
    report = {"harness": harness, "scenario": scenario, "settings": settings, "stock_version": STOCK_VERSION, "plugin_commit": PLUGIN_COMMIT if harness == "oh-my-opencode" else None,
              "plugin_source_evidence": PLUGIN_EVIDENCE if harness == "oh-my-opencode" else [], "comparison_basis": "Raw request bytes after explicitly guarded deviations; all other wire representation remains strict", "environment_regexes": [], "deviations_used": deviations,
              "workspace": str(workspace),
              "stock_oracle_selection": {"rule": stock_control_flow_ties.__doc__, "max_attempts": 5,
                                         "selected_attempt": selected, "attempts": attempts,
                                         "bb_runs": bb_runs, "comparison_max_bb_runs": 5},
              "bb_sidecar_oracle_selection": {
                  "rule": stock_control_flow_ties.__doc__, "max_attempts": 5,
                  "selected_attempt": bb_selected, "attempts": bb_attempts,
                  "native_source": "BB sidecar's own unmodified stock records; message-v2.ts:740 time-tie defect",
                  "rejection_signals": ["Native tie containing a summary assistant or compaction part"],
                  "bytes_or_counts_can_reject": False,
              },
              "native_prune_fences": prune_fences,
              "overflow_native_timing": {
                  "configuration_fact_not_deviation": True,
                  "race_evidence": str(ROOT / "artifacts/compaction_lanes/native-stock-defects/recovery_timer_prune_race.json"),
                  "real_human_ack_turns_before_overflow": 2,
                  "error_delivery": "After the prior turn's six owned native message.part.updated prune events on both sides",
                  "expected_native_branch": "Six original outputs PATCHed, insufficient; summarize fallbacks",
                  "same_original_tool_outputs_and_error": True,
                  "removed_response_descriptors": 0,
                  "sleeps_or_fake_parts_or_terminals": False,
                  "production_timer_or_prune_changes": False,
              } if partial_overflow else None,
              "stock_defects": [{"name": "time_created_tie_changes_native_compaction_control_flow",
                                "source": "packages/opencode/src/session/message-v2.ts:740",
                                "observed_attempt": str(ROOT / "artifacts/compaction_lanes/omo-plugin-error-proof-r1/plugin_error"),
                                "native_database": "stock_runtime/data/opencode/opencode.db#message",
                                "native_timestamps": [1791679068733, 1791679068737],
                                "native_completed_summaries": 3,
                                "plugin_summarize_posts": 2,
                                "response": "Reject native control-flow ties before comparison; BB never clones the race",
                                "bb_deviation": False},
                               *([{"name": "authenticated_handled_truncation_prompt_async_missing_parts",
                                   "source": "src/hooks/anthropic-context-window-limit-recovery/aggressive-truncation-strategy.ts:61-77; packages/opencode/src/session/prompt.ts:111",
                                   "evidence": str(ROOT / "artifacts/compaction_lanes/native-stock-defects/prompt_async_missing_parts.json"),
                                   "response": "Preserve native missing-parts failure, not a fabricated continuation; partial-overflow scenario uses the genuine summarize fallback",
                                   "bb_deviation": False}]
                                 if harness == "oh-my-opencode" else [])],
              "mock_script": {"identical_for_both_sides": True,
                              "sha256": hashlib.sha256(canonical(script)).hexdigest(),
                              "response_descriptors": len(descriptors),
                              "auxiliary_descriptors": auxiliary_script(scenario),
                              "deferred_recovery_attempts": settings["deferred_recovery_attempts"] if scenario in {"overflow", "plugin_error"} else 0,
                              "budget_source": f"breadboard_engine/compaction/presets/{settings['preset']}.yaml#triggers"},
              "shared_session_id": {"value": sides["stock"].get("session_id"), "source": "Stock /session created the empty fixture before either compared run; both stock CLI import.ts:134-169 and BB native executor import that same fixture", "delivery": "BB context.session_id before its first production request; no body rewriting", "fixture": str(seed_path)},
              "mcp_relay": {"upstreams": MCPRelay.UPSTREAMS, "readiness_order": list(MCPRelay.UPSTREAMS), "native_source": "mcp/index.ts:192-213,503-529", "wire_transformations": [], "independent_stock_proof": str(ROOT / "artifacts/compaction_lanes/omo-mcp-stock-order-proof/order_report.json")} if harness == "oh-my-opencode" else None,
              "out_of_scope_findings": [{"defect": "Legacy local LSP wrapper serialization recursion, integrator-reported; selected stock execution does not use this wrapper", "source": "breadboard_engine/conductor/components.py:579-585"}],
              "counts": counts, "diffs": diffs, "unresolved": unresolved, "compaction_findings": scenario_findings, "compaction_checks_passed": not scenario_findings and not any(result["unconsumed_responses"] or result["unconsumed_auxiliary_responses"] or "error" in result for result in sides.values()), "passed": not diffs and not unresolved and not scenario_findings,
              "raw_differences": raw_differences, "deviation_records": deviation_records,
              "admitted_created_time_tie_groups_per_request": [
                  {"request": index, "tie_groups": next(
                      (usage["tie_groups"] for usage in tie_usage if usage["request"] == index), 0)}
                  for index in range(max(len(records["stock"]), len(records["bb"])))],
              "raw_diffs_before_guarded_deviations": compare_records(records["stock"], records["bb"]) if deviations else diffs,
              "artifacts": {side: {"requests": str(out / f"{side}_requests.jsonl"), "result": str(out / f"{side}_result.json")} for side in sides}}
    (out / "lane_report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", choices=tuple(PROFILES), required=True)
    parser.add_argument("--scenario", choices=(*SCENARIOS, *PLUGIN_SCENARIOS, "all"), required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--child", choices=("stock", "bb"))
    parser.add_argument("--base-url")
    parser.add_argument("--stock-session-id")
    parser.add_argument("--mcp-relay")
    args = parser.parse_args()
    sessions = json.loads((args.out / "source_seed_sessions.json").read_bytes()) if args.child else None
    if args.child:
        if args.workspace is None:
            parser.error("--child requires the shared --workspace")
        if args.child == "stock":
            result = run_stock(args.harness, args.scenario, args.workspace, args.out / "stock_runtime", args.base_url, sessions, args.mcp_relay)
        else:
            result = run_bb(args.harness, args.scenario, args.workspace, args.base_url,
                            args.stock_session_id, args.out / "effective_episode_profile.json",
                            sessions, args.mcp_relay)
        (args.out / f"{args.child}_result.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
        raise SystemExit(1 if "error" in result else 0)
    available = (*SCENARIOS, *PLUGIN_SCENARIOS) if args.harness == "oh-my-opencode" else SCENARIOS
    scenarios = available if args.scenario == "all" else (args.scenario,)
    results = [run_scenario(args.harness, scenario, args.out / scenario) for scenario in scenarios]
    print(json.dumps([{key: report[key] for key in ("harness", "scenario", "counts", "passed", "unresolved")} | {"diff_count": len(report["diffs"])} for report in results], indent=2))
    raise SystemExit(0 if all(report["passed"] for report in results) else 1)
