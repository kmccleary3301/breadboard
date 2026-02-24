#!/usr/bin/env python3
"""
Rank replay discrepancy signals into actionable vs expected drift buckets.

Inputs:
- visual diff metrics (`metrics.json`) from prior-vs-current fullpane comparisons
- ground-truth eval (`comparison_metrics.json`) from frozen ANSI evaluation
- footer QC pack (`footer_qc_pack.json`)

Output:
- JSON ranking with normalized risk scores and proposed actions
- Markdown summary for quick review
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object JSON at {path}")
    return payload


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _infer_footer_contrast_from_outputs(payload: dict[str, Any], footer_qc_dir: Path | None) -> dict[str, Any]:
    """
    Backfill footer contrast metrics from output crops when older footer packs don't include them.
    """
    if footer_qc_dir is None:
        return {}
    outputs = payload.get("outputs", {})
    if not isinstance(outputs, dict):
        return {}
    rel = str(outputs.get("final_crop") or "").strip()
    if not rel:
        return {}
    img_path = (footer_qc_dir / rel).resolve()
    if not img_path.exists():
        return {}
    try:
        img = Image.open(img_path).convert("RGB")
    except Exception:
        return {}
    px = list(img.getdata())
    if not px:
        return {}
    chans = [[rgb[i] for rgb in px] for i in range(3)]
    bg = [sorted(ch)[len(ch) // 2] for ch in chans]
    deltas = [max(abs(rgb[0] - bg[0]), abs(rgb[1] - bg[1]), abs(rgb[2] - bg[2])) for rgb in px]
    if not deltas:
        return {}
    sorted_d = sorted(deltas)

    def _pct(p: float) -> float:
        idx = int(round((len(sorted_d) - 1) * p))
        idx = max(0, min(len(sorted_d) - 1, idx))
        return float(sorted_d[idx])

    total = max(1, len(deltas))
    return {
        "background_rgb": [int(x) for x in bg],
        "max_channel_delta": float(max(deltas)),
        "p99_channel_delta": _pct(0.99),
        "p995_channel_delta": _pct(0.995),
        "ratio_delta_gt_20": float(sum(1 for d in deltas if d > 20) / total),
        "ratio_delta_gt_40": float(sum(1 for d in deltas if d > 40) / total),
    }


def _load_allowlist(path: Path | None) -> dict[str, dict[str, Any]]:
    if path is None or not path.exists():
        return {}
    payload = _load_json(path)
    out: dict[str, dict[str, Any]] = {}

    items = payload.get("items")
    if isinstance(items, list):
        for row in items:
            if not isinstance(row, dict):
                continue
            key = str(row.get("key") or "").strip()
            if not key:
                continue
            out[key] = {
                "max_risk_score": _safe_float(row.get("max_risk_score"), 1.0),
                "note": str(row.get("note") or ""),
            }

    keys = payload.get("keys")
    if isinstance(keys, list):
        for raw in keys:
            key = str(raw or "").strip()
            if not key:
                continue
            out.setdefault(key, {"max_risk_score": 1.0, "note": ""})

    # Optional compact map form: {"by_key": {"key": {"max_risk_score": ...}}}
    by_key = payload.get("by_key")
    if isinstance(by_key, dict):
        for raw_key, raw_cfg in by_key.items():
            key = str(raw_key or "").strip()
            if not key:
                continue
            cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
            out[key] = {
                "max_risk_score": _safe_float(cfg.get("max_risk_score"), 1.0),
                "note": str(cfg.get("note") or ""),
            }
    return out


@dataclass(frozen=True)
class QueueItem:
    key: str
    category: str
    severity: str
    risk_score: float
    summary: str
    evidence: str
    action: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "category": self.category,
            "severity": self.severity,
            "risk_score": round(self.risk_score, 4),
            "summary": self.summary,
            "evidence": self.evidence,
            "action": self.action,
        }


def _apply_allowlist(items: list[QueueItem], allowlist: dict[str, dict[str, Any]]) -> list[QueueItem]:
    if not allowlist:
        return items
    out: list[QueueItem] = []
    for item in items:
        cfg = allowlist.get(item.key)
        if not isinstance(cfg, dict):
            out.append(item)
            continue
        max_risk = _safe_float(cfg.get("max_risk_score"), 1.0)
        note = str(cfg.get("note") or "")
        if item.risk_score <= max_risk:
            out.append(
                QueueItem(
                    key=item.key,
                    category=f"{item.category}:known",
                    severity="info",
                    risk_score=0.0,
                    summary=f"Known/accepted gap: {item.summary}",
                    evidence=f"{item.evidence}; allowlist_note={note or 'none'}; allowlist_max_risk={max_risk:.3f}",
                    action="No immediate action; keep monitoring this known gap in replay reports.",
                )
            )
            continue
        out.append(
            QueueItem(
                key=item.key,
                category=item.category,
                severity="high",
                risk_score=item.risk_score,
                summary=f"{item.summary} (known-gap budget exceeded)",
                evidence=f"{item.evidence}; allowlist_note={note or 'none'}; allowlist_max_risk={max_risk:.3f}",
                action="Known-gap allowance exceeded; either fix drift or re-baseline allowlist budget with explicit justification.",
            )
        )
    return out


def _classify_visual_drift(metrics: dict[str, Any]) -> list[QueueItem]:
    rows = metrics.get("rows_sorted", [])
    if not isinstance(rows, list):
        return []

    items: list[QueueItem] = []
    for row in rows[:12]:
        if not isinstance(row, dict):
            continue
        scenario = str(row.get("scenario") or "")
        frame = str(row.get("frame") or "")
        changed = _safe_float(row.get("changed_px_pct"))
        mean_abs = _safe_float(row.get("mean_abs_diff"))
        rms = _safe_float(row.get("rms_diff"))
        header = _safe_float(row.get("changed_px_pct_header"))
        transcript = _safe_float(row.get("changed_px_pct_transcript"))
        footer = _safe_float(row.get("changed_px_pct_footer"))
        dominant_band = str(row.get("dominant_band") or "transcript")
        bbox = row.get("diff_bbox")
        bbox_str = "none"
        if isinstance(bbox, dict):
            bbox_str = (
                f"x={_safe_int(bbox.get('x'))} y={_safe_int(bbox.get('y'))} "
                f"w={_safe_int(bbox.get('width'))} h={_safe_int(bbox.get('height'))} "
                f"area_pct={_safe_float(bbox.get('area_pct')):.3f}"
            )
        key = f"visual:{scenario}:{frame}"
        evidence = (
            f"changed_px_pct={changed:.3f} mean_abs={mean_abs:.3f} rms={rms:.3f} "
            f"bands(header={header:.3f},transcript={transcript:.3f},footer={footer:.3f},dominant={dominant_band}) "
            f"bbox={bbox_str}"
        )

        if frame == "frame_0001":
            items.append(
                QueueItem(
                    key=key,
                    category="visual-drift",
                    severity="info",
                    risk_score=min(1.0, changed / 100.0),
                    summary="Large frame-0001 drift likely due startup transcript suppression / clean-first-frame behavior.",
                    evidence=evidence,
                    action="Keep excluded from blocking parity; track separately as startup-policy drift.",
                )
            )
            continue

        # Overlay-heavy scenarios often drift when close/focus policy changes.
        if scenario in {"subagents_v1_fullpane_v7", "todo_preview_v1_fullpane_v7"} and frame >= "frame_0004":
            sev = "medium" if changed >= 2.0 else "low"
            items.append(
                QueueItem(
                    key=key,
                    category="visual-drift",
                    severity=sev,
                    risk_score=min(1.0, changed / 20.0),
                    summary="Overlay/focus closeout presentation drift (likely policy/layout update, not corruption).",
                    evidence=evidence,
                    action="Review against intended final-state contract; preserve if deliberate, otherwise align footer/overlay close behavior.",
                )
            )
            continue

        if changed <= 0.0:
            items.append(
                QueueItem(
                    key=key,
                    category="visual-drift",
                    severity="info",
                    risk_score=0.0,
                    summary="No residual visual drift in non-bootstrap frame.",
                    evidence=evidence,
                    action="No action required; keep under regression monitoring.",
                )
            )
            continue

        if dominant_band == "footer":
            summary = "Residual visual drift concentrated in footer/status region."
            action = "Inspect footer/right-rail/status-line spacing and contrast before touching transcript rendering."
        elif dominant_band == "header":
            summary = "Residual visual drift concentrated in header/top chrome."
            action = "Inspect header/title/phase-line spacing and border rendering alignment."
        else:
            summary = "Residual visual drift concentrated in transcript region."
            action = "Inspect transcript row ordering, wrapping, and block spacing; preserve footer/header if unaffected."

        sev = "medium" if changed >= 1.0 else "low"
        items.append(
            QueueItem(
                key=key,
                category="visual-drift",
                severity=sev,
                risk_score=min(1.0, changed / 20.0),
                summary=summary,
                evidence=evidence,
                action=action,
            )
        )
    return items


def _classify_ground_truth(eval_metrics: dict[str, Any]) -> list[QueueItem]:
    targets = eval_metrics.get("targets", {})
    if not isinstance(targets, dict):
        return []

    items: list[QueueItem] = []
    for target, payload in targets.items():
        if not isinstance(payload, dict):
            continue
        limits = payload.get("limits", {}) if isinstance(payload.get("limits"), dict) else {}
        aggregate = payload.get("aggregate", {}) if isinstance(payload.get("aggregate"), dict) else {}
        row_occ = aggregate.get("row_occupancy", {}) if isinstance(aggregate.get("row_occupancy"), dict) else {}

        mae = _safe_float((aggregate.get("mae_rgb") or {}).get("mean"))
        rmse = _safe_float((aggregate.get("rmse_rgb") or {}).get("mean"))
        dim = _safe_float((aggregate.get("dim_abs_sum") or {}).get("mean"))
        missing = _safe_float((row_occ.get("missing_count") or {}).get("mean"))
        extra = _safe_float((row_occ.get("extra_count") or {}).get("mean"))
        span = _safe_float((row_occ.get("row_span_delta") or {}).get("mean"))

        mae_limit = max(0.0001, _safe_float(limits.get("max_mae_rgb"), 1.0))
        rmse_limit = max(0.0001, _safe_float(limits.get("max_rmse_rgb"), 1.0))
        dim_limit = max(0.0001, _safe_float(limits.get("max_dim_abs_sum"), 1.0))
        miss_limit = max(0.0001, _safe_float(limits.get("max_missing_text_rows"), 1.0))
        extra_limit = max(0.0001, _safe_float(limits.get("max_extra_render_rows"), 1.0))
        span_limit = max(0.0001, _safe_float(limits.get("max_row_span_delta"), 1.0))

        max_ratio = max(
            mae / mae_limit,
            rmse / rmse_limit,
            dim / dim_limit,
            missing / miss_limit if miss_limit > 0 else 0.0,
            extra / extra_limit,
            span / span_limit,
        )

        severity = "low"
        if max_ratio >= 0.9:
            severity = "high"
        elif max_ratio >= 0.75:
            severity = "medium"

        items.append(
            QueueItem(
                key=f"ground-truth:{target}",
                category="ground-truth",
                severity=severity,
                risk_score=min(1.0, max_ratio),
                summary=f"Ground-truth alignment headroom for {target}.",
                evidence=(
                    f"mae={mae:.4f}/{mae_limit:.4f} rmse={rmse:.4f}/{rmse_limit:.4f} "
                    f"dim={dim:.4f}/{dim_limit:.4f} missing={missing:.4f}/{miss_limit:.4f} "
                    f"extra={extra:.4f}/{extra_limit:.4f} span={span:.4f}/{span_limit:.4f}"
                ),
                action="If risk >= medium, inspect heatmaps and consider profile-level micro-tuning only with regression gate reruns.",
            )
        )
    return items


def _classify_footer_qc(footer: dict[str, Any], footer_qc_dir: Path | None = None) -> list[QueueItem]:
    targets = footer.get("targets", {})
    if not isinstance(targets, dict):
        return []

    items: list[QueueItem] = []
    for key, payload in targets.items():
        if not isinstance(payload, dict):
            continue
        final_contrast = payload.get("final_footer_contrast", {})
        if not isinstance(final_contrast, dict) or not final_contrast:
            final_contrast = _infer_footer_contrast_from_outputs(payload, footer_qc_dir)
        final_p99 = _safe_float(final_contrast.get("p99_channel_delta")) if isinstance(final_contrast, dict) else 0.0
        final_p995 = _safe_float(final_contrast.get("p995_channel_delta")) if isinstance(final_contrast, dict) else 0.0
        final_ratio20 = (
            _safe_float(final_contrast.get("ratio_delta_gt_20")) if isinstance(final_contrast, dict) else 0.0
        )
        contrast_risk = 0.0
        contrast_severity = "info"
        contrast_summary = ""
        if isinstance(final_contrast, dict) and final_contrast:
            # Guardrail for blank/under-rendered footers that diff-only checks can miss.
            if final_p99 < 8.0 or final_ratio20 < 0.0005:
                contrast_severity = "high"
                contrast_risk = 0.95
                contrast_summary = "Footer appears blank or severely under-rendered."
            elif final_p99 < 24.0 or final_ratio20 < 0.003:
                contrast_severity = "medium"
                contrast_risk = 0.65
                contrast_summary = "Footer legibility appears weak relative to baseline captures."
        diff = payload.get("prev_vs_new_final_footer_diff")
        if not isinstance(diff, dict):
            sev = contrast_severity
            risk = contrast_risk
            summary = "No previous-final footer reference available for direct numeric diff."
            if contrast_summary:
                summary = f"{contrast_summary} No previous-final footer reference available for direct numeric diff."
            items.append(
                QueueItem(
                    key=f"footer:{key}",
                    category="footer-qc",
                    severity=sev,
                    risk_score=risk,
                    summary=summary,
                    evidence=(
                        "prev_vs_new_final_footer_diff=missing "
                        f"final_contrast(p99={final_p99:.2f},p995={final_p995:.2f},gt20={final_ratio20:.5f})"
                    ),
                    action=(
                        "Investigate footer render/crop integrity before signoff."
                        if sev in {"medium", "high"}
                        else "Keep visual inspection snapshots in pack; add numeric baseline if needed."
                    ),
                )
            )
            continue
        mean_rgb = diff.get("mean_abs_diff_rgb") if isinstance(diff.get("mean_abs_diff_rgb"), list) else [0, 0, 0]
        rms_rgb = diff.get("rms_diff_rgb") if isinstance(diff.get("rms_diff_rgb"), list) else [0, 0, 0]
        mean_score = max(_safe_float(x) for x in mean_rgb)
        rms_score = max(_safe_float(x) for x in rms_rgb)
        severity = "info" if mean_score == 0.0 and rms_score == 0.0 else "low"
        if mean_score > 2.0 or rms_score > 8.0:
            severity = "medium"
        risk = min(1.0, max(mean_score / 6.0, rms_score / 24.0))
        summary = f"Footer final-state diff for {key}."
        action = "If non-zero, inspect x3 footer crops and tune footer contrast/spacing only if drift is unintentional."
        if contrast_risk > risk:
            severity = contrast_severity
            risk = contrast_risk
        if contrast_summary:
            summary = f"{contrast_summary} Footer final-state diff for {key}."
            if contrast_severity in {"medium", "high"}:
                action = "Inspect footer crop selection and render integrity before tuning visual styling."
        items.append(
            QueueItem(
                key=f"footer:{key}",
                category="footer-qc",
                severity=severity,
                risk_score=risk,
                summary=summary,
                evidence=(
                    f"mean_abs_rgb={mean_rgb} rms_rgb={rms_rgb} "
                    f"final_contrast(p99={final_p99:.2f},p995={final_p995:.2f},gt20={final_ratio20:.5f})"
                ),
                action=action,
            )
        )
    return items


def _classify_advanced_stress(advanced: dict[str, Any]) -> list[QueueItem]:
    if not isinstance(advanced, dict) or not advanced:
        return []

    items: list[QueueItem] = []
    validated = _safe_int(advanced.get("validated_count"))
    scenario_count = _safe_int(advanced.get("scenario_count"))
    overall_ok = bool(advanced.get("overall_ok"))
    missing = advanced.get("missing_scenarios", [])
    missing_count = len(missing) if isinstance(missing, list) else 0
    records = advanced.get("records", [])
    if not isinstance(records, list):
        records = []

    if missing_count > 0:
        items.append(
            QueueItem(
                key="advanced:missing-scenarios",
                category="advanced-stress",
                severity="high",
                risk_score=min(1.0, missing_count / max(1, scenario_count)),
                summary="Advanced stress suite missing replay scenarios.",
                evidence=f"missing={missing_count}/{scenario_count}",
                action="Recapture missing advanced scenarios before trusting stress coverage.",
            )
        )

    failed: list[tuple[str, list[str]]] = []
    for record in records:
        if not isinstance(record, dict):
            continue
        scenario = str(record.get("scenario") or "unknown")
        checks = record.get("checks", [])
        if not isinstance(checks, list):
            continue
        failed_checks = [str(row.get("name")) for row in checks if isinstance(row, dict) and not bool(row.get("pass"))]
        if failed_checks:
            failed.append((scenario, failed_checks))
            items.append(
                QueueItem(
                    key=f"advanced:{scenario}",
                    category="advanced-stress",
                    severity="high",
                    risk_score=min(1.0, 0.55 + 0.1 * len(failed_checks)),
                    summary="Advanced stress checks failed for scenario.",
                    evidence=f"failed_checks={', '.join(failed_checks)}",
                    action="Inspect run artifacts and fix advanced behavior regressions before signoff.",
                )
            )

    if failed:
        items.append(
            QueueItem(
                key="advanced:summary",
                category="advanced-stress",
                severity="high",
                risk_score=min(1.0, len(failed) / max(1, validated)),
                summary="Advanced stress suite has failing scenarios.",
                evidence=f"failed={len(failed)} validated={validated} missing={missing_count}",
                action="Block signoff until advanced scenario failures are resolved.",
            )
        )
    elif overall_ok and validated > 0:
        items.append(
            QueueItem(
                key="advanced:overall",
                category="advanced-stress",
                severity="info",
                risk_score=0.0,
                summary="Advanced stress suite passed.",
                evidence=f"validated={validated} missing={missing_count}",
                action="Keep this gate in the replay roundtrip pipeline.",
            )
        )

    return items


def _classify_artifact_integrity(artifact: dict[str, Any]) -> list[QueueItem]:
    if not isinstance(artifact, dict) or not artifact:
        return []
    items: list[QueueItem] = []
    records = artifact.get("records", [])
    if not isinstance(records, list):
        records = []
    missing = artifact.get("missing_scenarios", [])
    missing_count = len(missing) if isinstance(missing, list) else 0
    if missing_count > 0:
        items.append(
            QueueItem(
                key="artifact:missing-scenarios",
                category="artifact-integrity",
                severity="high",
                risk_score=min(1.0, missing_count / max(1, _safe_int(len(records) + missing_count))),
                summary="Artifact integrity checker missing required scenarios.",
                evidence=f"missing={missing_count}",
                action="Regenerate missing replay scenarios before relying on integrity coverage.",
            )
        )
    failing = 0
    for record in records:
        if not isinstance(record, dict):
            continue
        scenario = str(record.get("scenario") or "unknown")
        if bool(record.get("ok")):
            continue
        failing += 1
        metrics = record.get("metrics", {}) if isinstance(record.get("metrics"), dict) else {}
        missing_artifacts = _safe_int(metrics.get("missing_artifacts_count"))
        parity = _safe_int(metrics.get("row_parity_violations"))
        line_gap = _safe_int(metrics.get("line_gap_nonzero_count"))
        isolated = _safe_int(metrics.get("isolated_low_edge_rows_total"))
        risk = min(1.0, 0.4 + 0.08 * (missing_artifacts + line_gap) + 0.02 * parity + 0.01 * isolated)
        items.append(
            QueueItem(
                key=f"artifact:{scenario}",
                category="artifact-integrity",
                severity="high",
                risk_score=risk,
                summary="Artifact integrity failure in replay scenario.",
                evidence=f"missing={missing_artifacts} parity={parity} line_gap={line_gap} isolated={isolated}",
                action="Inspect localized integrity report and resolve rendering/capture corruption signals.",
            )
        )
    if failing == 0 and bool(artifact.get("overall_ok")):
        items.append(
            QueueItem(
                key="artifact:overall",
                category="artifact-integrity",
                severity="info",
                risk_score=0.0,
                summary="Artifact integrity suite passed.",
                evidence=f"validated={len(records)} missing={missing_count}",
                action="Keep artifact integrity check in recurring replay roundtrip.",
            )
        )
    elif failing > 0:
        items.append(
            QueueItem(
                key="artifact:summary",
                category="artifact-integrity",
                severity="high",
                risk_score=min(1.0, failing / max(1, len(records))),
                summary="One or more artifact integrity scenarios failed.",
                evidence=f"failing={failing} validated={len(records)} missing={missing_count}",
                action="Block signoff until artifact integrity failures are fixed.",
            )
        )
    return items


def _classify_reliability_flake(report: dict[str, Any]) -> list[QueueItem]:
    if not isinstance(report, dict) or not report:
        return []
    scenarios = report.get("scenarios", [])
    if not isinstance(scenarios, list):
        scenarios = []
    intermittent = 0
    transient = 0
    persistent = 0
    active = 0
    for row in scenarios:
        if not isinstance(row, dict):
            continue
        cls = str(row.get("classification") or "")
        if cls == "transient-single-recovery":
            transient += 1
        elif cls == "intermittent-flake":
            intermittent += 1
        elif cls == "persistent-fail":
            persistent += 1
        elif cls == "active-regression":
            active += 1
    if persistent > 0 or active > 0:
        return [
            QueueItem(
                key="reliability-flake:regression",
                category="reliability-flake",
                severity="high",
                risk_score=min(1.0, 0.8 + 0.1 * (persistent + active)),
                summary="Reliability flake report contains persistent or active regressions.",
                evidence=(
                    f"persistent={persistent} active={active} intermittent={intermittent} transient={transient}"
                ),
                action="Investigate affected scenarios before accepting replay signoff.",
            )
        ]
    if transient > 0 and intermittent == 0:
        return [
            QueueItem(
                key="reliability-flake:transient",
                category="reliability-flake",
                severity="info",
                risk_score=0.0,
                summary="Reliability telemetry shows transient single-run failures with clean recovery.",
                evidence=f"transient={transient} intermittent={intermittent} persistent={persistent} active={active}",
                action="No immediate action; continue monitoring for repeated recurrences.",
            )
        ]
    if intermittent > 0:
        return [
            QueueItem(
                key="reliability-flake:intermittent",
                category="reliability-flake",
                severity="low",
                risk_score=min(1.0, 0.2 + 0.05 * intermittent),
                summary="Reliability flake report shows intermittent failures.",
                evidence=(
                    f"intermittent={intermittent} transient={transient} persistent={persistent} active={active}"
                ),
                action="Monitor intermittent scenarios and prioritize if recurrence frequency increases.",
            )
        ]
    return [
        QueueItem(
            key="reliability-flake:stable",
            category="reliability-flake",
            severity="info",
            risk_score=0.0,
            summary="Reliability flake report is stable.",
            evidence=f"intermittent={intermittent} transient={transient} persistent={persistent} active={active}",
            action="No action required; continue tracking flake telemetry.",
        )
    ]


def _canary_waits_expected(canary_hotspot: Any) -> bool:
    if not isinstance(canary_hotspot, dict):
        return False
    summary = canary_hotspot.get("gap_summary", {})
    if not isinstance(summary, dict):
        return False
    top_wait_dominated = _safe_int(summary.get("top_wait_dominated_count"), 0)
    top_expected_wait = _safe_int(summary.get("top_expected_wait_count"), 0)
    top_unexpected_wait = _safe_int(summary.get("top_unexpected_wait_count"), 0)
    return top_wait_dominated > 0 and top_expected_wait > 0 and top_unexpected_wait == 0


def _classify_latency_trend(latency: dict[str, Any]) -> list[QueueItem]:
    if not isinstance(latency, dict) or not latency:
        return []
    items: list[QueueItem] = []
    summary = latency.get("summary", {})
    thresholds = latency.get("thresholds", {})
    if not isinstance(summary, dict):
        summary = {}
    if not isinstance(thresholds, dict):
        thresholds = {}
    missing_count = _safe_int(summary.get("missing_count"))
    record_count = _safe_int(summary.get("record_count"))
    ok_count = _safe_int(summary.get("ok_count"))
    if missing_count > 0:
        items.append(
            QueueItem(
                key="latency:missing-scenarios",
                category="latency-trend",
                severity="high",
                risk_score=min(1.0, missing_count / max(1, record_count + missing_count)),
                summary="Latency trend report missing required scenarios.",
                evidence=f"missing={missing_count} records={record_count}",
                action="Refresh missing scenarios before interpreting latency trends.",
            )
        )
    failed_records = max(0, record_count - ok_count)
    if failed_records > 0:
        items.append(
            QueueItem(
                key="latency:failed-records",
                category="latency-trend",
                severity="high",
                risk_score=min(1.0, failed_records / max(1, record_count)),
                summary="Latency thresholds failed in one or more scenarios.",
                evidence=f"failed={failed_records} records={record_count}",
                action="Investigate replay pacing regressions before UI polish signoff.",
            )
        )

    def _ratio(metric: str, stat: str = "max") -> float:
        m = summary.get(metric, {})
        if not isinstance(m, dict):
            return 0.0
        observed = _safe_float(m.get(stat), 0.0)
        lim_key = metric if metric.startswith("max_") else f"max_{metric}"
        if lim_key not in thresholds and lim_key == "max_non_wait_frame_gap_seconds":
            lim_key = "max_frame_gap_seconds"
        limit = _safe_float(thresholds.get(lim_key), 1.0)
        if limit <= 0:
            return 0.0
        return observed / limit

    max_ratio = max(
        _ratio("first_frame_seconds"),
        _ratio("replay_first_frame_seconds"),
        _ratio("duration_seconds"),
        _ratio("max_stall_seconds"),
        _ratio("max_frame_gap_seconds"),
    )
    near = latency.get("near_threshold", {})
    canary_expected_waits = _canary_waits_expected(latency.get("_canary_hotspot"))
    near_top = near.get("top", []) if isinstance(near, dict) else []
    top_contributor = near_top[0] if isinstance(near_top, list) and near_top else {}
    contributor_str = ""
    top_metric = ""
    top_wait_overlap = False
    if isinstance(top_contributor, dict) and top_contributor:
        top_metric = str(top_contributor.get("metric") or "")
        top_wait_overlap = bool(top_contributor.get("wait_overlap"))
        contributor_str = (
            f" top={top_contributor.get('scenario')}:{top_contributor.get('metric')}@"
            f"{_safe_float(top_contributor.get('ratio')):.3f}"
        )
        if "wait_overlap" in top_contributor:
            contributor_str += f" wait_overlap={top_wait_overlap}"
    if max_ratio >= 0.9 and failed_records == 0:
        if top_metric == "max_frame_gap_seconds" and top_wait_overlap:
            non_wait_ratio = _ratio("max_non_wait_frame_gap_seconds")
            non_wait_max = _safe_float(
                (summary.get("max_non_wait_frame_gap_seconds") or {}).get("max")
                if isinstance(summary.get("max_non_wait_frame_gap_seconds"), dict)
                else 0.0
            )
            non_wait_limit = _safe_float(thresholds.get("max_frame_gap_seconds"), 0.0)
            expected_wait_evidence = ""
            if canary_expected_waits:
                hotspot_summary = latency.get("_canary_hotspot", {}).get("gap_summary", {})
                if isinstance(hotspot_summary, dict):
                    expected_wait_evidence = (
                        f" expected_waits={_safe_int(hotspot_summary.get('top_expected_wait_count'))}"
                        f" unexpected_waits={_safe_int(hotspot_summary.get('top_unexpected_wait_count'))}"
                    )
            items.append(
                QueueItem(
                    key="latency:near-threshold",
                    category="latency-trend",
                    severity="info" if canary_expected_waits else "low",
                    risk_score=0.0 if canary_expected_waits else min(1.0, max_ratio * 0.65),
                    summary=(
                        "Latency near-threshold canary is fully explained by expected harness waits."
                        if canary_expected_waits
                        else "Latency near-threshold is wait-overlap dominated on max-frame-gap canary."
                    ),
                    evidence=f"max_ratio={max_ratio:.3f}{contributor_str}{expected_wait_evidence}",
                    action=(
                        "Keep monitoring canary trend; prioritize non-wait frame-gap and first-frame metrics for optimization."
                        if canary_expected_waits
                        else "Treat as harness-influenced canary; prioritize non-wait frame-gap and first-frame metrics for optimization."
                    ),
                )
            )
            non_wait_sev = "info" if non_wait_ratio < 0.85 else "low"
            non_wait_summary = (
                "Non-wait frame-gap has headroom under current threshold."
                if non_wait_sev == "info"
                else "Non-wait frame-gap is also approaching threshold."
            )
            non_wait_action = (
                "Keep monitoring non-wait frame-gap trend; no immediate optimization required."
                if non_wait_sev == "info"
                else "Prioritize non-wait rendering cadence optimization for top scenarios."
            )
            items.append(
                QueueItem(
                    key="latency:non-wait-frame-gap",
                    category="latency-trend",
                    severity=non_wait_sev,
                    risk_score=0.0 if non_wait_sev == "info" else min(1.0, non_wait_ratio * 0.75),
                    summary=non_wait_summary,
                    evidence=(
                        f"non_wait_max_ratio={non_wait_ratio:.3f} "
                        f"non_wait_max={non_wait_max:.3f}/{non_wait_limit:.3f}"
                    ),
                    action=non_wait_action,
                )
            )
            return items
        items.append(
            QueueItem(
                key="latency:near-threshold",
                category="latency-trend",
                severity="medium",
                risk_score=min(1.0, max_ratio),
                summary="Latency trend is near threshold on at least one metric.",
                evidence=f"max_ratio={max_ratio:.3f}{contributor_str}",
                action="Watch trend deltas closely; prioritize optimization for the top contributing scenario/metric pair.",
            )
        )
    elif failed_records == 0 and missing_count == 0 and bool(latency.get("overall_ok")):
        items.append(
            QueueItem(
                key="latency:overall",
                category="latency-trend",
                severity="info",
                risk_score=0.0,
                summary="Latency trend suite passed.",
                evidence=f"records={record_count} max_ratio={max_ratio:.3f}",
                action="Keep replay latency trend report in recurring QC runs.",
            )
        )
    return items


def _classify_latency_drift_gate(gate: dict[str, Any]) -> list[QueueItem]:
    if not isinstance(gate, dict) or not gate:
        return []
    items: list[QueueItem] = []
    overall_ok = bool(gate.get("overall_ok"))
    ratio = _safe_float(gate.get("worst_threshold_ratio"), 0.0)
    metric = str(gate.get("worst_threshold_ratio_metric") or "")
    baseline_present = bool(gate.get("baseline_present"))
    worst_context = gate.get("worst_metric_context", {})
    context_wait_overlap = bool(worst_context.get("wait_overlap")) if isinstance(worst_context, dict) else False
    context_scenario = str(worst_context.get("scenario") or "") if isinstance(worst_context, dict) else ""
    canary_expected_waits = _canary_waits_expected(gate.get("canary_hotspot"))
    if not overall_ok:
        items.append(
            QueueItem(
                key="latency-drift:gate-fail",
                category="latency-drift-gate",
                severity="high",
                risk_score=min(1.0, max(0.75, ratio)),
                summary="Latency drift gate failed.",
                evidence=f"worst_ratio={ratio:.3f} metric={metric}",
                action="Block signoff and investigate replay pacing drift before continuing polish.",
            )
        )
    elif not baseline_present:
        items.append(
            QueueItem(
                key="latency-drift:bootstrap",
                category="latency-drift-gate",
                severity="info",
                risk_score=0.0,
                summary="Latency drift gate in bootstrap mode (no baseline).",
                evidence=f"worst_ratio={ratio:.3f} metric={metric}",
                action="Allow first run; ensure next run has baseline to enable drift deltas.",
            )
        )
    else:
        sev = "low"
        if ratio >= 0.9:
            sev = "medium"
        risk = min(1.0, ratio)
        summary = "Latency drift gate passed."
        action = "Keep monitoring this canary in every replay roundtrip."
        evidence = f"worst_ratio={ratio:.3f} metric={metric}"
        if metric == "max_frame_gap_seconds" and context_wait_overlap:
            sev = "info" if canary_expected_waits else "low"
            risk = 0.0 if canary_expected_waits else min(1.0, ratio * 0.65)
            summary = (
                "Latency drift gate passed (max-frame-gap canary aligns with expected harness waits)."
                if canary_expected_waits
                else "Latency drift gate passed (worst metric is wait-overlap-dominated max-frame-gap)."
            )
            action = (
                "Monitor non-wait frame-gap and first-frame ratios for true regressions."
                if canary_expected_waits
                else "Treat as harness-influenced canary; monitor non-wait frame-gap and first-frame ratios for true regressions."
            )
            evidence = f"worst_ratio={ratio:.3f} metric={metric} scenario={context_scenario} wait_overlap=True"
        items.append(
            QueueItem(
                key="latency-drift:pass",
                category="latency-drift-gate",
                severity=sev,
                risk_score=risk,
                summary=summary,
                evidence=evidence,
                action=action,
            )
        )
    return items


def _build_markdown(
    *,
    items: list[QueueItem],
    visual_path: Path,
    ground_truth_path: Path,
    footer_path: Path,
    advanced_path: Path | None,
    reliability_flake_path: Path | None,
    artifact_path: Path | None,
    latency_path: Path | None,
    latency_drift_gate_path: Path | None,
    canary_hotspot_path: Path | None,
    allowlist_path: Path | None,
) -> str:
    lines: list[str] = [
        "# Phase5 Replay Discrepancy Ranking",
        "",
        "## Inputs",
        f"- Visual metrics: `{visual_path}`",
        f"- Ground-truth metrics: `{ground_truth_path}`",
        f"- Footer QC pack: `{footer_path}`",
    ]
    if advanced_path is not None:
        lines.append(f"- Advanced stress report: `{advanced_path}`")
    if reliability_flake_path is not None:
        lines.append(f"- Reliability flake report: `{reliability_flake_path}`")
    if artifact_path is not None:
        lines.append(f"- Artifact integrity report: `{artifact_path}`")
    if latency_path is not None:
        lines.append(f"- Latency trend report: `{latency_path}`")
    if latency_drift_gate_path is not None:
        lines.append(f"- Latency drift gate: `{latency_drift_gate_path}`")
    if canary_hotspot_path is not None:
        lines.append(f"- Canary hotspot report: `{canary_hotspot_path}`")
    if allowlist_path is not None:
        lines.append(f"- Allowlist: `{allowlist_path}`")
    lines.extend(
        [
            "",
            "## Ranked Queue",
            "| Rank | Category | Severity | Risk Score | Summary | Evidence | Action |",
            "|---:|---|---|---:|---|---|---|",
        ]
    )
    for idx, item in enumerate(items, start=1):
        lines.append(
            f"| {idx} | {item.category} | {item.severity} | {item.risk_score:.4f} | "
            f"{item.summary} | {item.evidence} | {item.action} |"
        )
    return "\n".join(lines).rstrip() + "\n"


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--visual-metrics", required=True)
    p.add_argument("--ground-truth-metrics", required=True)
    p.add_argument("--footer-qc-pack", required=True)
    p.add_argument("--advanced-stress", required=False, default=None)
    p.add_argument("--reliability-flake", required=False, default=None)
    p.add_argument("--artifact-integrity", required=False, default=None)
    p.add_argument("--latency-trend", required=False, default=None)
    p.add_argument("--latency-drift-gate", required=False, default=None)
    p.add_argument("--canary-hotspot", required=False, default=None)
    p.add_argument("--allowlist-json", required=False, default=None)
    p.add_argument("--out-json", required=True)
    p.add_argument("--out-md", required=True)
    return p.parse_args()


def main() -> None:
    args = parse_args()
    visual_path = Path(args.visual_metrics).expanduser().resolve()
    ground_truth_path = Path(args.ground_truth_metrics).expanduser().resolve()
    footer_path = Path(args.footer_qc_pack).expanduser().resolve()
    advanced_path = Path(args.advanced_stress).expanduser().resolve() if args.advanced_stress else None
    reliability_flake_path = (
        Path(args.reliability_flake).expanduser().resolve() if args.reliability_flake else None
    )
    artifact_path = Path(args.artifact_integrity).expanduser().resolve() if args.artifact_integrity else None
    latency_path = Path(args.latency_trend).expanduser().resolve() if args.latency_trend else None
    latency_drift_gate_path = Path(args.latency_drift_gate).expanduser().resolve() if args.latency_drift_gate else None
    canary_hotspot_path = Path(args.canary_hotspot).expanduser().resolve() if args.canary_hotspot else None
    allowlist_path = Path(args.allowlist_json).expanduser().resolve() if args.allowlist_json else None
    out_json = Path(args.out_json).expanduser().resolve()
    out_md = Path(args.out_md).expanduser().resolve()

    visual = _load_json(visual_path)
    gt = _load_json(ground_truth_path)
    footer = _load_json(footer_path)
    advanced = _load_json(advanced_path) if advanced_path is not None and advanced_path.exists() else {}
    reliability_flake = (
        _load_json(reliability_flake_path)
        if reliability_flake_path is not None and reliability_flake_path.exists()
        else {}
    )
    artifact = _load_json(artifact_path) if artifact_path is not None and artifact_path.exists() else {}
    latency = _load_json(latency_path) if latency_path is not None and latency_path.exists() else {}
    latency_drift_gate = (
        _load_json(latency_drift_gate_path)
        if latency_drift_gate_path is not None and latency_drift_gate_path.exists()
        else {}
    )
    canary_hotspot = (
        _load_json(canary_hotspot_path) if canary_hotspot_path is not None and canary_hotspot_path.exists() else {}
    )
    if isinstance(latency, dict):
        latency["_canary_hotspot"] = canary_hotspot
    if isinstance(latency_drift_gate, dict):
        latency_drift_gate["canary_hotspot"] = canary_hotspot
    allowlist = _load_allowlist(allowlist_path)

    items = [
        *_classify_ground_truth(gt),
        *_classify_footer_qc(footer, footer_path.parent),
        *_classify_advanced_stress(advanced),
        *_classify_reliability_flake(reliability_flake),
        *_classify_artifact_integrity(artifact),
        *_classify_latency_trend(latency),
        *_classify_latency_drift_gate(latency_drift_gate),
        *_classify_visual_drift(visual),
    ]
    items = _apply_allowlist(items, allowlist)
    items.sort(key=lambda item: item.risk_score, reverse=True)

    payload = {
        "schema_version": "phase5_replay_discrepancy_ranking_v1",
        "inputs": {
            "visual_metrics": str(visual_path),
            "ground_truth_metrics": str(ground_truth_path),
            "footer_qc_pack": str(footer_path),
            "advanced_stress": str(advanced_path) if advanced_path is not None else None,
            "reliability_flake": str(reliability_flake_path) if reliability_flake_path is not None else None,
            "artifact_integrity": str(artifact_path) if artifact_path is not None else None,
            "latency_trend": str(latency_path) if latency_path is not None else None,
            "latency_drift_gate": str(latency_drift_gate_path) if latency_drift_gate_path is not None else None,
            "canary_hotspot": str(canary_hotspot_path) if canary_hotspot_path is not None else None,
            "allowlist_json": str(allowlist_path) if allowlist_path is not None else None,
        },
        "count": len(items),
        "allowlist_count": len(allowlist),
        "items": [item.as_dict() for item in items],
    }

    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    out_md.write_text(
        _build_markdown(
            items=items,
            visual_path=visual_path,
            ground_truth_path=ground_truth_path,
            footer_path=footer_path,
            advanced_path=advanced_path,
            reliability_flake_path=reliability_flake_path,
            artifact_path=artifact_path,
            latency_path=latency_path,
            latency_drift_gate_path=latency_drift_gate_path,
            canary_hotspot_path=canary_hotspot_path,
            allowlist_path=allowlist_path,
        ),
        encoding="utf-8",
    )
    print(f"items={len(items)}")
    print(f"json={out_json}")
    print(f"md={out_md}")


if __name__ == "__main__":
    main()
