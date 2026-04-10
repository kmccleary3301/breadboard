from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List, Mapping, Sequence

from .kits import SearchStudyKit, build_search_study_kit
from .study import (
    SearchStudyRegistry,
    SearchStudyRunResult,
    compare_search_study_runs,
    run_search_study,
)


@dataclass(frozen=True)
class SearchOperatorBadge:
    label: str
    value: str
    tone: str

    def to_dict(self) -> Dict[str, str]:
        return {"label": self.label, "value": self.value, "tone": self.tone}


@dataclass(frozen=True)
class SearchOperatorPanel:
    panel_id: str
    title: str
    lines: tuple[str, ...]
    artifact_refs: tuple[str, ...] = ()
    badges: tuple[SearchOperatorBadge, ...] = ()

    def to_dict(self) -> Dict[str, object]:
        return {
            "panel_id": self.panel_id,
            "title": self.title,
            "lines": list(self.lines),
            "artifact_refs": list(self.artifact_refs),
            "badges": [badge.to_dict() for badge in self.badges],
        }


@dataclass(frozen=True)
class SearchOperatorScreen:
    study_key: str
    title: str
    mode: str
    summary_line: str
    badges: tuple[SearchOperatorBadge, ...]
    panels: tuple[SearchOperatorPanel, ...]
    commands: tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {
            "study_key": self.study_key,
            "title": self.title,
            "mode": self.mode,
            "summary_line": self.summary_line,
            "badges": [badge.to_dict() for badge in self.badges],
            "panels": [panel.to_dict() for panel in self.panels],
            "commands": list(self.commands),
        }

    def to_text(self) -> str:
        lines = [f"{self.title} [{self.mode}]", self.summary_line]
        if self.badges:
            badge_bits = [f"{badge.label}={badge.value}" for badge in self.badges]
            lines.append("badges: " + ", ".join(badge_bits))
        if self.commands:
            lines.append("commands: " + " | ".join(self.commands))
        for panel in self.panels:
            lines.append("")
            lines.append(f"[{panel.title}]")
            lines.extend(panel.lines)
            if panel.badges:
                panel_bits = [f"{badge.label}={badge.value}" for badge in panel.badges]
                lines.append("panel_badges: " + ", ".join(panel_bits))
            if panel.artifact_refs:
                lines.append("artifacts: " + ", ".join(panel.artifact_refs[:6]))
        return "\n".join(lines)


@dataclass(frozen=True)
class SearchOperatorCompareScreen:
    left_study_key: str
    right_study_key: str
    title: str
    summary_line: str
    panels: tuple[SearchOperatorPanel, ...]
    commands: tuple[str, ...]

    def to_dict(self) -> Dict[str, object]:
        return {
            "left_study_key": self.left_study_key,
            "right_study_key": self.right_study_key,
            "title": self.title,
            "summary_line": self.summary_line,
            "panels": [panel.to_dict() for panel in self.panels],
            "commands": list(self.commands),
        }

    def to_text(self) -> str:
        lines = [self.title, self.summary_line]
        if self.commands:
            lines.append("commands: " + " | ".join(self.commands))
        for panel in self.panels:
            lines.append("")
            lines.append(f"[{panel.title}]")
            lines.extend(panel.lines)
        return "\n".join(lines)


def _metric_tone(metric_key: str, metric_value: object) -> str:
    if metric_key in {"decision", "freeze_decision"}:
        return "decision"
    if metric_key == "topology_class":
        return "topology"
    if metric_key == "gap_label":
        return "warning"
    if isinstance(metric_value, bool):
        return "ok" if metric_value else "warning"
    return "neutral"


def _build_badges(summary_json: Mapping[str, object]) -> tuple[SearchOperatorBadge, ...]:
    badges: List[SearchOperatorBadge] = [
        SearchOperatorBadge("phase", str(summary_json.get("phase", "unknown")), "phase"),
        SearchOperatorBadge("family", str(summary_json.get("packet_family", "unknown")), "family"),
    ]
    metrics = summary_json.get("top_level_metrics")
    if isinstance(metrics, Mapping):
        for key in ("topology_class", "candidate_set_size", "gap_label", "decision"):
            if key in metrics:
                badges.append(
                    SearchOperatorBadge(
                        key,
                        str(metrics[key]),
                        _metric_tone(key, metrics[key]),
                    )
                )
    selected_candidate_id = summary_json.get("selected_candidate_id")
    if selected_candidate_id:
        badges.append(SearchOperatorBadge("selected", str(selected_candidate_id), "selection"))
    return tuple(badges)


def _overview_panel(result: SearchStudyRunResult, kit: SearchStudyKit) -> SearchOperatorPanel:
    summary = result.summary_json
    metrics = summary.get("top_level_metrics")
    metric_bits: List[str] = []
    if isinstance(metrics, Mapping):
        metric_bits = [f"{key}: {value}" for key, value in sorted(metrics.items())]
    lines = [
        f"study_key: {summary['study_key']}",
        f"title: {summary['title']}",
        f"packet_family: {summary['packet_family']}",
        f"tags: {', '.join(summary['tags']) if summary['tags'] else 'none'}",
        (
            "artifact_contract: "
            f"summary_json={kit.artifact_contract.summary_json}, "
            f"summary_txt={kit.artifact_contract.summary_txt}, "
            f"inspect={kit.artifact_contract.inspect_supported}, "
            f"compare={kit.artifact_contract.compare_supported}, "
            f"open_artifact={kit.artifact_contract.open_artifact_supported}, "
            f"artifact_refs={kit.artifact_contract.artifact_ref_count}"
        ),
    ]
    lines.extend(metric_bits[:6])
    return SearchOperatorPanel(
        panel_id="overview",
        title="Overview",
        lines=tuple(lines),
        artifact_refs=result.artifact_refs[:6],
        badges=_build_badges(summary),
    )


def _control_panel(kit: SearchStudyKit) -> SearchOperatorPanel:
    lines = [
        f"{template.control_key} [{template.status}]: {template.summary}"
        for template in kit.control_templates
    ]
    return SearchOperatorPanel(
        panel_id="controls",
        title="Controls",
        lines=tuple(lines),
    )


def _inspection_panel(result: SearchStudyRunResult) -> SearchOperatorPanel | None:
    inspection = result.inspect()
    lines: List[str] = []
    if "lineage_view" in inspection:
        lineage = inspection["lineage_view"]
        if isinstance(lineage, Mapping):
            lines.append(
                "lineage: "
                f"events={lineage.get('event_count')} "
                f"operators={len(lineage.get('operator_kinds', []))} "
                f"frontiers={len(lineage.get('frontier_ids', []))}"
            )
    if "assessment_chain_view" in inspection:
        chain = inspection["assessment_chain_view"]
        if isinstance(chain, Mapping):
            lines.append(
                "assessment_chain: "
                f"kinds={len(chain.get('assessment_kinds', []))} "
                f"links={chain.get('action_link_count')} "
                f"reconstructable={chain.get('mixed_chain_reconstructable')}"
            )
    if "replay_export_summary" in inspection:
        replay = inspection["replay_export_summary"]
        if isinstance(replay, Mapping):
            lines.append(
                "replay_export: "
                f"preserved={len(replay.get('preserved_semantics', []))} "
                f"lost={len(replay.get('lost_semantics', []))} "
                f"shadow_assumptions={replay.get('shadow_assumptions_required')}"
            )
    if not lines:
        return None
    return SearchOperatorPanel(
        panel_id="inspection",
        title="Inspection",
        lines=tuple(lines),
    )


def _packet_focus_panel(packet: Mapping[str, object]) -> SearchOperatorPanel | None:
    lines: List[str] = []
    if "consumer_seam_diagnostic" in packet:
        diagnostic = packet["consumer_seam_diagnostic"]
        if hasattr(diagnostic, "to_dict") and callable(diagnostic.to_dict):
            diagnostic = diagnostic.to_dict()
        if isinstance(diagnostic, Mapping):
            lines.append(
                "consumer_seam: "
                f"common={len(diagnostic.get('common_seam_labels', []))} "
                f"helper_only={len(diagnostic.get('helper_only_seam_labels', []))} "
                f"consumer_local={len(diagnostic.get('consumer_local_seam_labels', []))}"
            )
    if "domain_friction_summary" in packet:
        friction = packet["domain_friction_summary"]
        if hasattr(friction, "to_dict") and callable(friction.to_dict):
            friction = friction.to_dict()
        if isinstance(friction, Mapping):
            lines.append(
                "domain_friction: "
                f"classification={friction.get('dominant_classification')} "
                f"requires_kernel={friction.get('requires_kernel_change')}"
            )
    if "pilot_packet" in packet:
        pilot = packet["pilot_packet"]
        if hasattr(pilot, "to_dict") and callable(pilot.to_dict):
            pilot = pilot.to_dict()
        if isinstance(pilot, Mapping):
            lines.append(
                "domain_friction: "
                f"classification={pilot.get('friction_locus')} "
                f"requires_kernel={pilot.get('kernel_change_required')}"
            )
    if "decision" in packet:
        lines.append(f"decision: {packet['decision']}")
    if "freeze_decision" in packet:
        lines.append(f"freeze_decision: {packet['freeze_decision']}")
    if not lines:
        return None
    return SearchOperatorPanel(
        panel_id="focus",
        title="Focus",
        lines=tuple(lines),
    )


def build_search_operator_screen(
    study_key: str,
    *,
    mode: str = "spec",
    registry: SearchStudyRegistry | None = None,
) -> SearchOperatorScreen:
    active_registry = registry
    result = run_search_study(study_key, mode=mode, registry=active_registry)
    kit = build_search_study_kit(study_key, registry=active_registry)
    panels: List[SearchOperatorPanel] = [
        _overview_panel(result, kit),
        _control_panel(kit),
    ]
    inspection_panel = _inspection_panel(result)
    if inspection_panel is not None:
        panels.append(inspection_panel)
    focus_panel = _packet_focus_panel(result.packet)
    if focus_panel is not None:
        panels.append(focus_panel)
    summary = result.summary_json
    summary_line = (
        f"{summary['study_key']} ({summary['packet_family']}) "
        f"artifacts={len(result.artifact_refs)} mode={mode}"
    )
    commands = (
        f"run_search_study('{study_key}', mode='{mode}')",
        f"inspect_search_study('{study_key}', mode='{mode}')",
        f"build_search_operator_screen('{study_key}', mode='{mode}')",
    )
    return SearchOperatorScreen(
        study_key=study_key,
        title=result.registry_entry.title,
        mode=mode,
        summary_line=summary_line,
        badges=_build_badges(summary),
        panels=tuple(panels),
        commands=commands,
    )


def build_search_operator_compare_screen(
    left_study_key: str,
    right_study_key: str,
    *,
    left_mode: str = "spec",
    right_mode: str = "spec",
    registry: SearchStudyRegistry | None = None,
) -> SearchOperatorCompareScreen:
    left = run_search_study(left_study_key, mode=left_mode, registry=registry)
    right = run_search_study(right_study_key, mode=right_mode, registry=registry)
    comparison = compare_search_study_runs(left, right)
    left_inspection = left.inspect()
    right_inspection = right.inspect()
    panels: List[SearchOperatorPanel] = [
        SearchOperatorPanel(
            panel_id="comparison",
            title="Comparison",
            lines=(
                f"shared_artifacts: {len(comparison['shared_artifact_refs'])}",
                "left_only_packet_keys: " + ", ".join(comparison["left_only_packet_keys"][:8]),
                "right_only_packet_keys: " + ", ".join(comparison["right_only_packet_keys"][:8]),
            ),
        )
    ]
    left_replay = left_inspection.get("replay_export_summary")
    right_replay = right_inspection.get("replay_export_summary")
    if isinstance(left_replay, Mapping) and isinstance(right_replay, Mapping):
        preserved_left = set(str(item) for item in left_replay.get("preserved_semantics", []))
        preserved_right = set(str(item) for item in right_replay.get("preserved_semantics", []))
        lost_left = set(str(item) for item in left_replay.get("lost_semantics", []))
        lost_right = set(str(item) for item in right_replay.get("lost_semantics", []))
        panels.append(
            SearchOperatorPanel(
                panel_id="replay_export_diff",
                title="Replay/Export Diff",
                lines=(
                    f"shared_preserved: {len(preserved_left & preserved_right)}",
                    f"left_only_preserved: {len(preserved_left - preserved_right)}",
                    f"right_only_preserved: {len(preserved_right - preserved_left)}",
                    f"shared_lost: {len(lost_left & lost_right)}",
                ),
            )
        )
    commands = (
        f"compare_search_study_runs(run_search_study('{left_study_key}'), run_search_study('{right_study_key}'))",
        f"build_search_operator_compare_screen('{left_study_key}', '{right_study_key}')",
    )
    return SearchOperatorCompareScreen(
        left_study_key=left_study_key,
        right_study_key=right_study_key,
        title=f"Compare {left_study_key} vs {right_study_key}",
        summary_line=f"{left_study_key}:{left_mode} vs {right_study_key}:{right_mode}",
        panels=tuple(panels),
        commands=commands,
    )


def render_search_operator_screen_text(screen: SearchOperatorScreen) -> str:
    return screen.to_text()


def render_search_operator_compare_screen_text(screen: SearchOperatorCompareScreen) -> str:
    return screen.to_text()
