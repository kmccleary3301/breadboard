#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def collect_slab_panes(dirs: list[Path], files: list[Path]) -> list[Path]:
    out: list[Path] = []
    for d in dirs:
        if d.exists():
            out.extend(sorted(d.glob("*_pane.txt")))
    out.extend(files)
    deduped: list[Path] = []
    seen: set[str] = set()
    for p in out:
        s = str(p)
        if s in seen:
            continue
        seen.add(s)
        deduped.append(p)
    return deduped


def reduce_latest_by_prefix(panes: list[Path]) -> list[Path]:
    latest: dict[str, Path] = {}
    for pane in panes:
        base = pane.name
        m = re.match(r"(.+)_\d{8}-\d{6}_pane\.txt$", base)
        prefix = m.group(1) if m else base
        prev = latest.get(prefix)
        if prev is None or pane.name > prev.name:
            latest[prefix] = pane
    return sorted(latest.values(), key=lambda p: p.name)


def parse_slab_markers(panes: list[Path]) -> dict[str, Any]:
    marker_counts: Counter[str] = Counter()
    per_file: dict[str, dict[str, bool]] = {}
    bridge_missing_files: list[str] = []

    for pane in panes:
        if not pane.exists():
            continue
        txt = read_text(pane)
        marks = {
            "has_bridge_missing": "bridge missing" in txt,
            "has_context_group": bool(re.search(r"\[context\]\s+\d+\s+ops", txt)),
            "has_context_toggle": "Context burst" in txt,
            "has_artifact_ref": "artifact" in txt and "tool_output" in txt,
            "has_artifact_diff": "tool_diff" in txt,
            "has_diff_preview": "diff --git" in txt,
            "has_subagent_strip": bool(re.search(r"Subagents\s+\[\d+/\d+\]", txt)),
            "has_task_surface": "[task]" in txt or "tasks " in txt,
            "has_session_line": "[session]" in txt,
            "has_run_finished": "[run_finished]" in txt,
            "has_footer_status": "OpenTUI slab (Phase C)" in txt,
            "has_subagent_nav_hint": "ctrl+←/→ cycle" in txt,
            "has_context_summary_line": "Context burst" in txt,
            "has_permission_marker": "permission" in txt.lower(),
            "has_retry_marker": "retry" in txt.lower(),
            "has_error_marker": "error" in txt.lower(),
            "has_cancel_marker": "cancel" in txt.lower(),
            "has_compaction_marker": "[transcript_compacted]" in txt,
        }
        per_file[str(pane)] = marks
        for k, v in marks.items():
            if v:
                marker_counts[k] += 1
        if marks["has_bridge_missing"]:
            bridge_missing_files.append(str(pane))

    return {
        "pane_count": len(per_file),
        "marker_counts": dict(marker_counts),
        "per_file": per_file,
        "bridge_missing_files": bridge_missing_files,
    }


def parse_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in read_text(path).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def parse_opencode_refs(stdout_jsonl_files: list[Path], event_log_files: list[Path]) -> dict[str, Any]:
    tool_counts: Counter[str] = Counter()
    row_type_counts: Counter[str] = Counter()
    event_type_counts: Counter[str] = Counter()
    has_artifact_output = False
    has_diff_preview = False
    has_artifact_output_fixture = False
    has_artifact_diff_fixture = False

    for f in stdout_jsonl_files:
        fstr = str(f)
        if "phaseB_artifact_output_ref_v1" in fstr:
            has_artifact_output_fixture = True
        if "phaseB_artifact_diff_ref_v1" in fstr:
            has_artifact_diff_fixture = True
        for row in parse_jsonl(f):
            rtype = row.get("type", "")
            row_type_counts[rtype] += 1
            part = row.get("part", {}) or {}
            if rtype == "tool_use":
                tool = part.get("tool")
                if tool:
                    tool_counts[tool] += 1
                state = part.get("state", {}) or {}
                if state.get("status") == "completed":
                    if tool in ("write", "edit", "apply_patch"):
                        has_artifact_output = True
                    metadata = state.get("metadata", {}) or {}
                    diff_text = metadata.get("diff")
                    if isinstance(diff_text, str) and (
                        "diff --git" in diff_text or "Index:" in diff_text or "@@" in diff_text
                    ):
                        has_diff_preview = True
                    files = metadata.get("files")
                    if isinstance(files, list):
                        for file_meta in files:
                            if not isinstance(file_meta, dict):
                                continue
                            if isinstance(file_meta.get("diff"), str) and file_meta.get("diff"):
                                has_diff_preview = True
                            if file_meta.get("type") in ("add", "update", "delete"):
                                has_artifact_output = True

    for f in event_log_files:
        for row in parse_jsonl(f):
            et = row.get("type")
            if et:
                event_type_counts[et] += 1

    marker = defaultdict(bool)
    marker["ref_has_context_tools"] = any(
        tool_counts.get(t, 0) > 0 for t in ("list", "read", "read_file", "grep", "glob")
    )
    marker["ref_has_subagent_tooling"] = any(
        tool_counts.get(t, 0) > 0 for t in ("task", "background_task", "background_output", "background_cancel")
    )
    marker["ref_has_background_cancel_flow"] = tool_counts.get("background_cancel", 0) > 0
    marker["ref_has_agent_spawn_events"] = event_type_counts.get("agent.spawned", 0) > 0
    marker["ref_has_agent_completion_events"] = event_type_counts.get("agent.job_completed", 0) > 0
    marker["ref_has_run_finished_events"] = event_type_counts.get("run.finished", 0) > 0
    marker["ref_has_artifact_output"] = has_artifact_output
    marker["ref_has_diff_preview"] = has_diff_preview
    marker["ref_has_artifact_output_fixture"] = has_artifact_output_fixture
    marker["ref_has_artifact_diff_fixture"] = has_artifact_diff_fixture

    return {
        "stdout_file_count": len(stdout_jsonl_files),
        "event_log_file_count": len(event_log_files),
        "tool_counts": dict(tool_counts),
        "row_type_counts": dict(row_type_counts),
        "event_type_counts": dict(event_type_counts),
        "markers": dict(marker),
    }


def status(ref: bool, slab: bool, comparable: bool = True) -> str:
    if not comparable:
        return "n/a"
    if ref and slab:
        return "pass"
    if ref and not slab:
        return "gap"
    return "partial"


def load_manifest_json(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {}
    if not path.exists():
        return {}
    try:
        return json.loads(read_text(path))
    except json.JSONDecodeError:
        return {}


def build_signal_maps(refs: dict[str, Any], slab: dict[str, Any]) -> tuple[dict[str, bool], dict[str, bool]]:
    mc = slab.get("marker_counts", {})
    rm = refs.get("markers", {})
    slab_signals = {
        "slab_has_context_group": mc.get("has_context_group", 0) > 0,
        "slab_has_subagent_strip": mc.get("has_subagent_strip", 0) > 0,
        "slab_has_task_surface": mc.get("has_task_surface", 0) > 0,
        "slab_has_subagent_strip_and_task_surface": (mc.get("has_subagent_strip", 0) > 0 and mc.get("has_task_surface", 0) > 0),
        "slab_has_artifact_ref": mc.get("has_artifact_ref", 0) > 0,
        "slab_has_artifact_diff_and_preview": (mc.get("has_artifact_diff", 0) > 0 and mc.get("has_diff_preview", 0) > 0),
        "slab_capture_hygiene_ok": len(slab.get("bridge_missing_files", [])) == 0,
        "slab_has_permission_marker": mc.get("has_permission_marker", 0) > 0,
        "slab_has_retry_and_error": (mc.get("has_retry_marker", 0) > 0 and mc.get("has_error_marker", 0) > 0),
        "slab_has_cancel_marker": mc.get("has_cancel_marker", 0) > 0,
        "slab_has_compaction_marker": mc.get("has_compaction_marker", 0) > 0,
    }
    ref_signals = {
        **{k: bool(v) for k, v in rm.items()},
        "ref_has_subagent_tooling_or_spawn": bool(rm.get("ref_has_subagent_tooling") or rm.get("ref_has_agent_spawn_events")),
        "const_true": True,
    }
    return slab_signals, ref_signals


def build_matrix(refs: dict[str, Any], slab: dict[str, Any], manifest: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    manifest = manifest or {}
    slab_signals, ref_signals = build_signal_maps(refs, slab)
    axes = manifest.get("axes")
    if isinstance(axes, list) and axes:
        rows: list[dict[str, Any]] = []
        for axis in axes:
            if not isinstance(axis, dict):
                continue
            axis_id = str(axis.get("axis", "")).strip()
            if not axis_id:
                continue
            ref_key = str(axis.get("reference_signal_key", "")).strip()
            slab_key = str(axis.get("slab_signal_key", "")).strip()
            comparable = bool(axis.get("comparable", True))
            comparable_key = str(axis.get("comparable_key", "")).strip()
            if comparable_key:
                comparable = bool(ref_signals.get(comparable_key, False))
            ref_value = bool(ref_signals.get(ref_key, False))
            slab_value = bool(slab_signals.get(slab_key, False))
            rows.append(
                {
                    "axis": axis_id,
                    "reference_signal": ref_value,
                    "slab_signal": slab_value,
                    "status": status(ref_value, slab_value, comparable=comparable),
                    "notes": str(axis.get("notes", "")),
                }
            )
        return rows

    rows = []
    rows.append(
        {
            "axis": "context_compaction",
            "reference_signal": bool(ref_signals.get("ref_has_context_tools")),
            "slab_signal": bool(slab_signals.get("slab_has_context_group")),
            "status": status(bool(ref_signals.get("ref_has_context_tools")), bool(slab_signals.get("slab_has_context_group"))),
            "notes": "Reference has read/list/grep/glob activity and slab emits grouped [context] ops blocks.",
        }
    )
    rows.append(
        {
            "axis": "subagent_lifecycle_surface",
            "reference_signal": bool(ref_signals.get("ref_has_subagent_tooling_or_spawn")),
            "slab_signal": bool(slab_signals.get("slab_has_subagent_strip_and_task_surface")),
            "status": status(
                bool(ref_signals.get("ref_has_subagent_tooling_or_spawn")),
                bool(slab_signals.get("slab_has_subagent_strip_and_task_surface")),
            ),
            "notes": "Reference shows task/background flows; slab shows strip + task counters.",
        }
    )
    rows.append(
        {
            "axis": "background_cancel_semantics",
            "reference_signal": bool(ref_signals.get("ref_has_background_cancel_flow")),
            "slab_signal": bool(slab_signals.get("slab_has_subagent_strip")),
            "status": status(bool(ref_signals.get("ref_has_background_cancel_flow")), bool(slab_signals.get("slab_has_subagent_strip"))),
            "notes": "Behavioral match only; no one-to-one text fixture for cancel flow in slab artifact set yet.",
        }
    )
    rows.append(
        {
            "axis": "artifact_output_compaction",
            "reference_signal": bool(ref_signals.get("ref_has_artifact_output")),
            "slab_signal": bool(slab_signals.get("slab_has_artifact_ref")),
            "status": status(
                bool(ref_signals.get("ref_has_artifact_output")),
                bool(slab_signals.get("slab_has_artifact_ref")),
                comparable=bool(ref_signals.get("ref_has_artifact_output_fixture")),
            ),
            "notes": "Matched via OpenCode artifact fixture rows (tool output + file artifact creation surface).",
        }
    )
    rows.append(
        {
            "axis": "diff_artifact_preview",
            "reference_signal": bool(ref_signals.get("ref_has_diff_preview")),
            "slab_signal": bool(slab_signals.get("slab_has_artifact_diff_and_preview")),
            "status": status(
                bool(ref_signals.get("ref_has_diff_preview")),
                bool(slab_signals.get("slab_has_artifact_diff_and_preview")),
                comparable=bool(ref_signals.get("ref_has_artifact_diff_fixture")),
            ),
            "notes": "Matched via OpenCode diff fixture rows (apply_patch/edit metadata diff payloads).",
        }
    )
    rows.append(
        {
            "axis": "capture_hygiene",
            "reference_signal": True,
            "slab_signal": len(slab.get("bridge_missing_files", [])) == 0,
            "status": status(True, len(slab.get("bridge_missing_files", [])) == 0),
            "notes": "Latest selected panes should be free of bridge-missing contamination.",
        }
    )
    return rows


def evaluate_manifest_scenarios(manifest: dict[str, Any], panes: list[Path]) -> list[dict[str, Any]]:
    scenarios = manifest.get("scenarios")
    if not isinstance(scenarios, list):
        return []
    by_prefix: dict[str, str] = {}
    for pane in panes:
        text = read_text(pane) if pane.exists() else ""
        m = re.match(r"(.+)_\d{8}-\d{6}_pane\.txt$", pane.name)
        prefix = m.group(1) if m else pane.name
        by_prefix[prefix] = text
    rows: list[dict[str, Any]] = []
    for scenario in scenarios:
        if not isinstance(scenario, dict):
            continue
        sid = str(scenario.get("id", "")).strip()
        if not sid:
            continue
        required = [str(t) for t in scenario.get("required_tokens", []) if str(t).strip()]
        txt = by_prefix.get(sid, "")
        missing = [token for token in required if token not in txt]
        rows.append(
            {
                "id": sid,
                "class": str(scenario.get("class", "")).strip(),
                "required_count": len(required),
                "missing_tokens": missing,
                "status": "pass" if not missing else "gap",
            }
        )
    return rows


def to_markdown(report: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# OpenTUI vs OpenCode Marker Parity Report")
    lines.append("")
    lines.append(f"Generated: {report['generated_at_utc']}")
    lines.append("")
    lines.append("## Inputs")
    lines.append("")
    lines.append(f"- slab pane files: {report['inputs']['slab_pane_count']}")
    lines.append(f"- OpenCode stdout jsonl files: {report['inputs']['opencode_stdout_count']}")
    lines.append(f"- OpenCode event-log jsonl files: {report['inputs']['opencode_event_log_count']}")
    if report["inputs"].get("manifest_json"):
        lines.append(f"- parity manifest: {report['inputs']['manifest_json']}")
    lines.append("")
    lines.append("## Matrix")
    lines.append("")
    lines.append("| Axis | Status | Reference Signal | Slab Signal | Notes |")
    lines.append("|---|---|---:|---:|---|")
    for row in report["matrix"]:
        lines.append(
            f"| {row['axis']} | {row['status']} | {str(row['reference_signal']).lower()} | {str(row['slab_signal']).lower()} | {row['notes']} |"
        )
    lines.append("")
    if report.get("scenario_coverage"):
        rows = report["scenario_coverage"]
        passed = len([r for r in rows if r.get("status") == "pass"])
        total = len(rows)
        lines.append("## Scenario Coverage")
        lines.append("")
        lines.append(f"- pass: {passed}/{total}")
        lines.append("")
        lines.append("| Scenario | Class | Status | Missing Tokens |")
        lines.append("|---|---|---|---|")
        for row in rows:
            missing = ", ".join(row.get("missing_tokens", []))
            lines.append(
                f"| {row.get('id','')} | {row.get('class','')} | {row.get('status','')} | {missing} |"
            )
        lines.append("")
    lines.append("## Slab marker counts")
    lines.append("")
    for k, v in sorted(report["slab"]["marker_counts"].items()):
        lines.append(f"- `{k}`: {v}")
    lines.append("")
    if report["slab"]["bridge_missing_files"]:
        lines.append("## Bridge-missing panes")
        lines.append("")
        for p in report["slab"]["bridge_missing_files"]:
            lines.append(f"- `{p}`")
    else:
        lines.append("No bridge-missing panes found in selected slab files.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slab-dir", action="append", default=[])
    ap.add_argument("--slab-file", action="append", default=[])
    ap.add_argument("--opencode-stdout-jsonl", action="append", default=[])
    ap.add_argument("--opencode-event-log-jsonl", action="append", default=[])
    ap.add_argument("--all-pane-revisions", action="store_true", help="Use all pane files; default uses latest per scenario prefix.")
    ap.add_argument("--manifest-json", default="")
    ap.add_argument("--output-json", required=True)
    ap.add_argument("--output-md", required=True)
    args = ap.parse_args()

    slab_dirs = [Path(p) for p in args.slab_dir]
    slab_files = [Path(p) for p in args.slab_file]
    ref_stdout = [Path(p) for p in args.opencode_stdout_jsonl]
    ref_event_logs = [Path(p) for p in args.opencode_event_log_jsonl]
    manifest_path = Path(args.manifest_json).resolve() if str(args.manifest_json).strip() else None
    manifest = load_manifest_json(manifest_path)

    panes_all = collect_slab_panes(slab_dirs, slab_files)
    panes = panes_all if args.all_pane_revisions else reduce_latest_by_prefix(panes_all)
    slab = parse_slab_markers(panes)
    refs = parse_opencode_refs(ref_stdout, ref_event_logs)
    matrix = build_matrix(refs, slab, manifest=manifest)
    scenario_coverage = evaluate_manifest_scenarios(manifest, panes)

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "inputs": {
            "slab_pane_count": len(panes),
            "opencode_stdout_count": len(ref_stdout),
            "opencode_event_log_count": len(ref_event_logs),
            "manifest_json": str(manifest_path) if manifest_path else "",
            "slab_panes": [str(p) for p in panes],
            "opencode_stdout_jsonl": [str(p) for p in ref_stdout],
            "opencode_event_log_jsonl": [str(p) for p in ref_event_logs],
        },
        "manifest": manifest,
        "slab": slab,
        "references": refs,
        "matrix": matrix,
        "scenario_coverage": scenario_coverage,
    }

    out_json = Path(args.output_json)
    out_md = Path(args.output_md)
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    out_md.write_text(to_markdown(report), encoding="utf-8")

    print(f"[parity-markers] wrote {out_json}")
    print(f"[parity-markers] wrote {out_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
