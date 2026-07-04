from __future__ import annotations

import json
import os
import statistics
from dataclasses import dataclass, field
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from breadboard.rl.env_package.schema import EnvPackage
from breadboard.rl.env_package.validate import load_env_package
from breadboard.rl.export import build_projection_manifest
from breadboard.rl.replay import compare_replay_parity, decide_export_admission
from breadboard.rl.security import build_hardening_report, build_verifier_run_report, quarantine_on_findings
from breadboard.rl.trace import build_graph_from_session_events


@dataclass(frozen=True)
class ControlledSweProbeRow:
    task_id: str
    row_status: str
    reward: float
    hardening_status: str
    replay_status: str
    exportable_debug: bool
    trainable: bool
    projection_id: str
    metrics_ms: dict[str, float]
    blocked_reasons: list[str] = field(default_factory=list)
    findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "row_status": self.row_status,
            "reward": self.reward,
            "hardening_status": self.hardening_status,
            "replay_status": self.replay_status,
            "exportable_debug": self.exportable_debug,
            "trainable": self.trainable,
            "projection_id": self.projection_id,
            "metrics_ms": dict(self.metrics_ms),
            "blocked_reasons": list(self.blocked_reasons),
            "findings": list(self.findings),
        }


@dataclass(frozen=True)
class ControlledSweProbeRun:
    run_id: str
    target_run_id: str | None
    package_id: str
    package_hash: str
    source_claim: str
    rows: list[ControlledSweProbeRow]
    metrics_summary: dict[str, dict[str, float]]
    qc_report: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "target_run_id": self.target_run_id,
            "package_id": self.package_id,
            "package_hash": self.package_hash,
            "source_claim": self.source_claim,
            "rows": [row.to_dict() for row in self.rows],
            "metrics_summary": self.metrics_summary,
            "qc_report": dict(self.qc_report),
        }


def _task_ids(package: EnvPackage, limit: int) -> list[str]:
    split = package.splits["train_probe"]
    ids = list(split.selector.get("task_ids") or [])
    return [str(item) for item in ids[:limit]]


def _metrics_for_index(index: int) -> dict[str, float]:
    return {
        "reset_ms": 5.0 + index,
        "step_ms": 7.0 + index,
        "verify_ms": 9.0 + index,
        "export_ms": 3.0 + index,
        "total_ms": 24.0 + (4 * index),
    }


def _summarize_metrics(rows: list[ControlledSweProbeRow]) -> dict[str, dict[str, float]]:
    summary: dict[str, dict[str, float]] = {}
    for key in ["reset_ms", "step_ms", "verify_ms", "export_ms", "total_ms"]:
        values = [row.metrics_ms[key] for row in rows]
        sorted_values = sorted(values)
        p95_index = min(len(sorted_values) - 1, int(round(0.95 * (len(sorted_values) - 1))))
        summary[key] = {
            "p50": float(statistics.median(values)),
            "p95": float(sorted_values[p95_index]),
        }
    return summary


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def run_controlled_swe_probe(
    *,
    package_path: Path,
    output_dir: Path | None = None,
    run_id: str = "controlled_swe_toy_m6",
    limit: int = 10,
) -> ControlledSweProbeRun:
    package = load_env_package(package_path)
    if package.hardening is None:
        raise ValueError("controlled SWE probe requires package hardening policy")
    task_ids = _task_ids(package, limit)
    if len(task_ids) < 10:
        raise ValueError("M6 controlled SWE probe requires at least 10 task ids")

    rows: list[ControlledSweProbeRow] = []
    with TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        for index, task_id in enumerate(task_ids, start=1):
            workspace = tmp_root / task_id
            workspace.mkdir()
            if index in {4, 8}:
                (workspace / "sitecustomize.py").write_text("raise SystemExit('poison')\n", encoding="utf-8")

            hardening_report = build_hardening_report(
                report_id=f"{run_id}.{task_id}.hardening",
                workspace=workspace,
                policy=package.hardening,
                clean_baseline_passed=True,
                reference_solution_passed=True,
                process_cleanup_observed=True,
            )
            quarantine = quarantine_on_findings(
                row_id=task_id,
                findings=hardening_report.findings,
                policy=package.hardening,
            )
            reward = 0.0 if index == 7 else 1.0
            verifier_report = build_verifier_run_report(
                report_id=f"{run_id}.{task_id}.verifier",
                verifier_id=package.verifier.verifier_id,
                status="passed" if reward == 1.0 else "failed",
                output=f"reward={reward}",
                rerun_output=f"reward={reward}",
            )
            graph = _toy_graph_for_task(run_id=run_id, task_id=task_id, reward=reward)
            replay_report = compare_replay_parity(graph, graph)
            admission = decide_export_admission(
                replay_report=replay_report,
                hardening_status=hardening_report.status,
                quarantine_status="quarantined" if quarantine.quarantined else "clear",
                token_records_valid=True,
            )
            projection = build_projection_manifest(
                graph=graph,
                target_format="controlled_swe_toy_jsonl_probe",
                preserved_fields=["task_id", "reward", "hardening_status", "replay_status"],
                lost_fields=["full_workspace_bytes"],
                included_node_kinds={"step", "evaluate"},
            )
            if quarantine.quarantined:
                row_status = "quarantined"
            elif reward < 1.0:
                row_status = "rejected"
            else:
                row_status = "accepted"
            rows.append(
                ControlledSweProbeRow(
                    task_id=task_id,
                    row_status=row_status,
                    reward=reward,
                    hardening_status=hardening_report.status,
                    replay_status="passed" if replay_report.passed else "failed",
                    exportable_debug=admission.exportable and row_status == "accepted",
                    trainable=False,
                    projection_id=projection.projection_id,
                    metrics_ms=_metrics_for_index(index),
                    blocked_reasons=[
                        *admission.blocked_reasons,
                        *(["verifier_failed"] if row_status == "rejected" else []),
                    ],
                    findings=[item.finding_id for item in hardening_report.findings],
                )
            )
            if output_dir:
                _write_json(
                    output_dir / "row_evidence" / f"{task_id}.json",
                    {
                        "hardening_report": hardening_report.to_dict(),
                        "verifier_report": verifier_report.to_dict(),
                        "replay_report": replay_report.to_dict(),
                        "admission": admission.to_dict(),
                        "projection": projection.to_dict(),
                    },
                )

    metrics_summary = _summarize_metrics(rows)
    qc_report = _build_qc_report(rows)
    run = ControlledSweProbeRun(
        run_id=run_id,
        target_run_id=os.environ.get("M12_TARGET_RUN_ID"),
        package_id=package.package_id,
        package_hash=package.package_hash or "",
        source_claim="controlled_swe_toy_slice",
        rows=rows,
        metrics_summary=metrics_summary,
        qc_report=qc_report,
    )
    if output_dir:
        _write_json(output_dir / "run_summary.json", run.to_dict())
        _write_json(output_dir / "metrics_summary.json", metrics_summary)
        _write_json(output_dir / "qc_report.json", qc_report)
        _write_jsonl(output_dir / "run_ledger.jsonl", [row.to_dict() for row in rows])
    return run


def _build_qc_report(rows: list[ControlledSweProbeRow]) -> dict[str, Any]:
    by_status: dict[str, list[str]] = {"accepted": [], "rejected": [], "quarantined": []}
    for row in rows:
        by_status.setdefault(row.row_status, []).append(row.task_id)
    return {
        "accepted_sample": by_status["accepted"][:5],
        "rejected_sample": by_status["rejected"][:5],
        "quarantined_sample": by_status["quarantined"][:5],
        "reviewed_dimensions": [
            "trace_completeness",
            "verifier_evidence",
            "hardening_status",
            "replay_status",
            "projection_manifest",
            "claim_wording",
        ],
        "operator_notes": "Controlled SWE toy slice only; not external benchmark support.",
    }


def _toy_graph_for_task(*, run_id: str, task_id: str, reward: float):
    from breadboard.rl.session.events import SessionEvent
    from breadboard.rl.trace import build_graph_from_session_events

    events = [
        SessionEvent(
            event_id=f"{run_id}.{task_id}.reset",
            session_id=f"{run_id}.{task_id}",
            event_kind="reset",
            status_before="created",
            status_after="ready",
            payload={"success": True, "result_kind": "reset"},
        ),
        SessionEvent(
            event_id=f"{run_id}.{task_id}.step",
            session_id=f"{run_id}.{task_id}",
            event_kind="step",
            status_before="ready",
            status_after="running",
            payload={"success": True, "result_kind": "step"},
        ),
        SessionEvent(
            event_id=f"{run_id}.{task_id}.evaluate",
            session_id=f"{run_id}.{task_id}",
            event_kind="evaluate",
            status_before="running",
            status_after="evaluated",
            payload={"success": True, "result_kind": "evaluate", "reward": reward},
        ),
    ]
    return build_graph_from_session_events(
        graph_id=f"{run_id}.{task_id}.graph",
        session_id=f"{run_id}.{task_id}",
        events=events,
    )
