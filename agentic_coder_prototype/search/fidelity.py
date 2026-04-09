from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence

from .schema import SearchCandidate, SearchRun


def _require_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{field_name} must be non-empty")
    return text


def _copy_mapping(value: Mapping[str, Any] | None) -> Dict[str, Any]:
    return dict(value or {})


def _copy_text_list(values: Sequence[Any] | None) -> List[str]:
    copied: List[str] = []
    for item in values or []:
        text = str(item or "").strip()
        if text:
            copied.append(text)
    return copied


def _copy_mapping_list(values: Sequence[Mapping[str, Any]] | None) -> List[Dict[str, Any]]:
    return [dict(item) for item in (values or [])]


@dataclass(frozen=True)
class PaperRecipeManifest:
    manifest_id: str
    paper_key: str
    paper_title: str
    family_kind: str
    runtime_recipe_kind: str
    fidelity_target: str
    model_policy: str
    benchmark_packet: str
    control_profile: Dict[str, Any] = field(default_factory=dict)
    baseline_ids: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "manifest_id", _require_text(self.manifest_id, "manifest_id"))
        object.__setattr__(self, "paper_key", _require_text(self.paper_key, "paper_key"))
        object.__setattr__(self, "paper_title", _require_text(self.paper_title, "paper_title"))
        object.__setattr__(self, "family_kind", _require_text(self.family_kind, "family_kind"))
        object.__setattr__(self, "runtime_recipe_kind", _require_text(self.runtime_recipe_kind, "runtime_recipe_kind"))
        object.__setattr__(self, "fidelity_target", _require_text(self.fidelity_target, "fidelity_target"))
        object.__setattr__(self, "model_policy", _require_text(self.model_policy, "model_policy"))
        object.__setattr__(self, "benchmark_packet", _require_text(self.benchmark_packet, "benchmark_packet"))
        object.__setattr__(self, "control_profile", _copy_mapping(self.control_profile))
        object.__setattr__(self, "baseline_ids", _copy_text_list(self.baseline_ids))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))
        if not self.baseline_ids:
            raise ValueError("baseline_ids must contain at least one baseline id")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "manifest_id": self.manifest_id,
            "paper_key": self.paper_key,
            "paper_title": self.paper_title,
            "family_kind": self.family_kind,
            "runtime_recipe_kind": self.runtime_recipe_kind,
            "fidelity_target": self.fidelity_target,
            "model_policy": self.model_policy,
            "benchmark_packet": self.benchmark_packet,
            "control_profile": dict(self.control_profile),
            "baseline_ids": list(self.baseline_ids),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "PaperRecipeManifest":
        return PaperRecipeManifest(
            manifest_id=data.get("manifest_id") or "",
            paper_key=data.get("paper_key") or "",
            paper_title=data.get("paper_title") or "",
            family_kind=data.get("family_kind") or "",
            runtime_recipe_kind=data.get("runtime_recipe_kind") or "",
            fidelity_target=data.get("fidelity_target") or "",
            model_policy=data.get("model_policy") or "",
            benchmark_packet=data.get("benchmark_packet") or "",
            control_profile=dict(data.get("control_profile") or {}),
            baseline_ids=list(data.get("baseline_ids") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class FidelityScorecard:
    scorecard_id: str
    paper_key: str
    fidelity_label: str
    dimensions: Dict[str, str]
    notes: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "scorecard_id", _require_text(self.scorecard_id, "scorecard_id"))
        object.__setattr__(self, "paper_key", _require_text(self.paper_key, "paper_key"))
        object.__setattr__(self, "fidelity_label", _require_text(self.fidelity_label, "fidelity_label"))
        dimensions = {str(key): _require_text(value, f"dimensions[{key}]") for key, value in (self.dimensions or {}).items()}
        if not dimensions:
            raise ValueError("dimensions must contain at least one entry")
        object.__setattr__(self, "dimensions", dimensions)
        object.__setattr__(self, "notes", _copy_mapping(self.notes))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scorecard_id": self.scorecard_id,
            "paper_key": self.paper_key,
            "fidelity_label": self.fidelity_label,
            "dimensions": dict(self.dimensions),
            "notes": dict(self.notes),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "FidelityScorecard":
        return FidelityScorecard(
            scorecard_id=data.get("scorecard_id") or "",
            paper_key=data.get("paper_key") or "",
            fidelity_label=data.get("fidelity_label") or "",
            dimensions=dict(data.get("dimensions") or {}),
            notes=dict(data.get("notes") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ComputeBudgetLedger:
    ledger_id: str
    paper_key: str
    model_tier: str
    entries: List[Dict[str, Any]]
    normalization_rule: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ledger_id", _require_text(self.ledger_id, "ledger_id"))
        object.__setattr__(self, "paper_key", _require_text(self.paper_key, "paper_key"))
        object.__setattr__(self, "model_tier", _require_text(self.model_tier, "model_tier"))
        entries = [dict(item) for item in (self.entries or [])]
        if not entries:
            raise ValueError("entries must contain at least one item")
        for entry in entries:
            entry["entry_id"] = _require_text(entry.get("entry_id"), "entry_id")
            entry["kind"] = _require_text(entry.get("kind"), "kind")
            entry["label"] = _require_text(entry.get("label"), "label")
            entry["quantity"] = float(entry.get("quantity") or 0.0)
            entry["unit"] = _require_text(entry.get("unit"), "unit")
        object.__setattr__(self, "entries", entries)
        object.__setattr__(self, "normalization_rule", _require_text(self.normalization_rule, "normalization_rule"))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def total_for_unit(self, unit: str) -> float:
        unit_key = _require_text(unit, "unit")
        return sum(float(item["quantity"]) for item in self.entries if item["unit"] == unit_key)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ledger_id": self.ledger_id,
            "paper_key": self.paper_key,
            "model_tier": self.model_tier,
            "entries": [dict(item) for item in self.entries],
            "normalization_rule": self.normalization_rule,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ComputeBudgetLedger":
        return ComputeBudgetLedger(
            ledger_id=data.get("ledger_id") or "",
            paper_key=data.get("paper_key") or "",
            model_tier=data.get("model_tier") or "",
            entries=[dict(item) for item in data.get("entries") or []],
            normalization_rule=data.get("normalization_rule") or "",
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class BaselineComparisonPacket:
    packet_id: str
    paper_key: str
    normalization_rule: str
    baseline_ids: List[str]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "packet_id", _require_text(self.packet_id, "packet_id"))
        object.__setattr__(self, "paper_key", _require_text(self.paper_key, "paper_key"))
        object.__setattr__(self, "normalization_rule", _require_text(self.normalization_rule, "normalization_rule"))
        object.__setattr__(self, "baseline_ids", _copy_text_list(self.baseline_ids))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))
        if not self.baseline_ids:
            raise ValueError("baseline_ids must contain at least one baseline id")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "paper_key": self.paper_key,
            "normalization_rule": self.normalization_rule,
            "baseline_ids": list(self.baseline_ids),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "BaselineComparisonPacket":
        return BaselineComparisonPacket(
            packet_id=data.get("packet_id") or "",
            paper_key=data.get("paper_key") or "",
            normalization_rule=data.get("normalization_rule") or "",
            baseline_ids=list(data.get("baseline_ids") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ReplicationDeviationLedger:
    ledger_id: str
    paper_key: str
    deviations: List[Dict[str, Any]]
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "ledger_id", _require_text(self.ledger_id, "ledger_id"))
        object.__setattr__(self, "paper_key", _require_text(self.paper_key, "paper_key"))
        deviations = [dict(item) for item in (self.deviations or [])]
        if not deviations:
            raise ValueError("deviations must contain at least one item")
        for item in deviations:
            item["deviation_id"] = _require_text(item.get("deviation_id"), "deviation_id")
            item["severity"] = _require_text(item.get("severity"), "severity")
            item["summary"] = _require_text(item.get("summary"), "summary")
        object.__setattr__(self, "deviations", deviations)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ledger_id": self.ledger_id,
            "paper_key": self.paper_key,
            "deviations": [dict(item) for item in self.deviations],
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ReplicationDeviationLedger":
        return ReplicationDeviationLedger(
            ledger_id=data.get("ledger_id") or "",
            paper_key=data.get("paper_key") or "",
            deviations=[dict(item) for item in data.get("deviations") or []],
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class RepeatedShapeRegisterEntry:
    gap_label: str
    target_family: str
    topology_class: str
    where_it_appears: str
    current_workaround: str
    why_workaround_is_insufficient: str
    effect_on_fidelity_tier: str
    effect_on_replay_export: str
    primary_locus: str
    seen_in_other_targets: List[str] = field(default_factory=list)
    seen_in_consumers: List[str] = field(default_factory=list)
    helper_exhausted: bool = False
    counts_toward_review: bool = False
    notes: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "gap_label", _require_text(self.gap_label, "gap_label"))
        object.__setattr__(self, "target_family", _require_text(self.target_family, "target_family"))
        object.__setattr__(self, "topology_class", _require_text(self.topology_class, "topology_class"))
        object.__setattr__(self, "where_it_appears", _require_text(self.where_it_appears, "where_it_appears"))
        object.__setattr__(self, "current_workaround", _require_text(self.current_workaround, "current_workaround"))
        object.__setattr__(
            self,
            "why_workaround_is_insufficient",
            _require_text(self.why_workaround_is_insufficient, "why_workaround_is_insufficient"),
        )
        object.__setattr__(
            self,
            "effect_on_fidelity_tier",
            _require_text(self.effect_on_fidelity_tier, "effect_on_fidelity_tier"),
        )
        object.__setattr__(
            self,
            "effect_on_replay_export",
            _require_text(self.effect_on_replay_export, "effect_on_replay_export"),
        )
        object.__setattr__(self, "primary_locus", _require_text(self.primary_locus, "primary_locus"))
        object.__setattr__(self, "seen_in_other_targets", _copy_text_list(self.seen_in_other_targets))
        object.__setattr__(self, "seen_in_consumers", _copy_text_list(self.seen_in_consumers))
        object.__setattr__(self, "helper_exhausted", bool(self.helper_exhausted))
        object.__setattr__(self, "counts_toward_review", bool(self.counts_toward_review))
        object.__setattr__(self, "notes", _copy_mapping(self.notes))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "gap_label": self.gap_label,
            "target_family": self.target_family,
            "topology_class": self.topology_class,
            "where_it_appears": self.where_it_appears,
            "current_workaround": self.current_workaround,
            "why_workaround_is_insufficient": self.why_workaround_is_insufficient,
            "effect_on_fidelity_tier": self.effect_on_fidelity_tier,
            "effect_on_replay_export": self.effect_on_replay_export,
            "primary_locus": self.primary_locus,
            "seen_in_other_targets": list(self.seen_in_other_targets),
            "seen_in_consumers": list(self.seen_in_consumers),
            "helper_exhausted": self.helper_exhausted,
            "counts_toward_review": self.counts_toward_review,
            "notes": dict(self.notes),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "RepeatedShapeRegisterEntry":
        return RepeatedShapeRegisterEntry(
            gap_label=data.get("gap_label") or "",
            target_family=data.get("target_family") or "",
            topology_class=data.get("topology_class") or "",
            where_it_appears=data.get("where_it_appears") or "",
            current_workaround=data.get("current_workaround") or "",
            why_workaround_is_insufficient=data.get("why_workaround_is_insufficient") or "",
            effect_on_fidelity_tier=data.get("effect_on_fidelity_tier") or "",
            effect_on_replay_export=data.get("effect_on_replay_export") or "",
            primary_locus=data.get("primary_locus") or "",
            seen_in_other_targets=list(data.get("seen_in_other_targets") or []),
            seen_in_consumers=list(data.get("seen_in_consumers") or []),
            helper_exhausted=bool(data.get("helper_exhausted")),
            counts_toward_review=bool(data.get("counts_toward_review")),
            notes=dict(data.get("notes") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class TopologyAudit:
    audit_id: str
    target_family: str
    topology_class: str
    parentage_reconstructable: bool
    fan_flow_reconstructable: bool
    feedback_loop_reconstructable: bool
    shadow_state_required: bool
    notes: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "audit_id", _require_text(self.audit_id, "audit_id"))
        object.__setattr__(self, "target_family", _require_text(self.target_family, "target_family"))
        object.__setattr__(self, "topology_class", _require_text(self.topology_class, "topology_class"))
        object.__setattr__(self, "parentage_reconstructable", bool(self.parentage_reconstructable))
        object.__setattr__(self, "fan_flow_reconstructable", bool(self.fan_flow_reconstructable))
        object.__setattr__(self, "feedback_loop_reconstructable", bool(self.feedback_loop_reconstructable))
        object.__setattr__(self, "shadow_state_required", bool(self.shadow_state_required))
        object.__setattr__(self, "notes", _copy_mapping(self.notes))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "target_family": self.target_family,
            "topology_class": self.topology_class,
            "parentage_reconstructable": self.parentage_reconstructable,
            "fan_flow_reconstructable": self.fan_flow_reconstructable,
            "feedback_loop_reconstructable": self.feedback_loop_reconstructable,
            "shadow_state_required": self.shadow_state_required,
            "notes": dict(self.notes),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "TopologyAudit":
        return TopologyAudit(
            audit_id=data.get("audit_id") or "",
            target_family=data.get("target_family") or "",
            topology_class=data.get("topology_class") or "",
            parentage_reconstructable=bool(data.get("parentage_reconstructable")),
            fan_flow_reconstructable=bool(data.get("fan_flow_reconstructable")),
            feedback_loop_reconstructable=bool(data.get("feedback_loop_reconstructable")),
            shadow_state_required=bool(data.get("shadow_state_required")),
            notes=dict(data.get("notes") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class FrontierPolicyAudit:
    audit_id: str
    target_family: str
    topology_class: str
    select_prune_reconstructable: bool
    budget_conditioned_reconstructable: bool
    reopen_backtrack_reconstructable: bool
    consumer_can_explain_frontier: bool
    shadow_policy_required: bool
    notes: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "audit_id", _require_text(self.audit_id, "audit_id"))
        object.__setattr__(self, "target_family", _require_text(self.target_family, "target_family"))
        object.__setattr__(self, "topology_class", _require_text(self.topology_class, "topology_class"))
        object.__setattr__(self, "select_prune_reconstructable", bool(self.select_prune_reconstructable))
        object.__setattr__(self, "budget_conditioned_reconstructable", bool(self.budget_conditioned_reconstructable))
        object.__setattr__(self, "reopen_backtrack_reconstructable", bool(self.reopen_backtrack_reconstructable))
        object.__setattr__(self, "consumer_can_explain_frontier", bool(self.consumer_can_explain_frontier))
        object.__setattr__(self, "shadow_policy_required", bool(self.shadow_policy_required))
        object.__setattr__(self, "notes", _copy_mapping(self.notes))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "audit_id": self.audit_id,
            "target_family": self.target_family,
            "topology_class": self.topology_class,
            "select_prune_reconstructable": self.select_prune_reconstructable,
            "budget_conditioned_reconstructable": self.budget_conditioned_reconstructable,
            "reopen_backtrack_reconstructable": self.reopen_backtrack_reconstructable,
            "consumer_can_explain_frontier": self.consumer_can_explain_frontier,
            "shadow_policy_required": self.shadow_policy_required,
            "notes": dict(self.notes),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "FrontierPolicyAudit":
        return FrontierPolicyAudit(
            audit_id=data.get("audit_id") or "",
            target_family=data.get("target_family") or "",
            topology_class=data.get("topology_class") or "",
            select_prune_reconstructable=bool(data.get("select_prune_reconstructable")),
            budget_conditioned_reconstructable=bool(data.get("budget_conditioned_reconstructable")),
            reopen_backtrack_reconstructable=bool(data.get("reopen_backtrack_reconstructable")),
            consumer_can_explain_frontier=bool(data.get("consumer_can_explain_frontier")),
            shadow_policy_required=bool(data.get("shadow_policy_required")),
            notes=dict(data.get("notes") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class AssessmentLineagePacket:
    packet_id: str
    target_family: str
    topology_class: str
    assessment_kinds: List[str]
    action_links: List[Dict[str, Any]]
    mixed_chain_reconstructable: bool
    notes: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "packet_id", _require_text(self.packet_id, "packet_id"))
        object.__setattr__(self, "target_family", _require_text(self.target_family, "target_family"))
        object.__setattr__(self, "topology_class", _require_text(self.topology_class, "topology_class"))
        object.__setattr__(self, "assessment_kinds", _copy_text_list(self.assessment_kinds))
        if not self.assessment_kinds:
            raise ValueError("assessment_kinds must contain at least one kind")
        object.__setattr__(self, "action_links", _copy_mapping_list(self.action_links))
        object.__setattr__(self, "mixed_chain_reconstructable", bool(self.mixed_chain_reconstructable))
        object.__setattr__(self, "notes", _copy_mapping(self.notes))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "target_family": self.target_family,
            "topology_class": self.topology_class,
            "assessment_kinds": list(self.assessment_kinds),
            "action_links": [dict(item) for item in self.action_links],
            "mixed_chain_reconstructable": self.mixed_chain_reconstructable,
            "notes": dict(self.notes),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "AssessmentLineagePacket":
        return AssessmentLineagePacket(
            packet_id=data.get("packet_id") or "",
            target_family=data.get("target_family") or "",
            topology_class=data.get("topology_class") or "",
            assessment_kinds=list(data.get("assessment_kinds") or []),
            action_links=[dict(item) for item in data.get("action_links") or []],
            mixed_chain_reconstructable=bool(data.get("mixed_chain_reconstructable")),
            notes=dict(data.get("notes") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ReplayExportIntegrityPacket:
    packet_id: str
    target_family: str
    topology_class: str
    export_modes: List[str]
    preserved_semantics: List[str]
    lost_semantics: List[str]
    shadow_assumptions_required: bool
    notes: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "packet_id", _require_text(self.packet_id, "packet_id"))
        object.__setattr__(self, "target_family", _require_text(self.target_family, "target_family"))
        object.__setattr__(self, "topology_class", _require_text(self.topology_class, "topology_class"))
        object.__setattr__(self, "export_modes", _copy_text_list(self.export_modes))
        object.__setattr__(self, "preserved_semantics", _copy_text_list(self.preserved_semantics))
        object.__setattr__(self, "lost_semantics", _copy_text_list(self.lost_semantics))
        if not self.export_modes:
            raise ValueError("export_modes must contain at least one mode")
        object.__setattr__(self, "shadow_assumptions_required", bool(self.shadow_assumptions_required))
        object.__setattr__(self, "notes", _copy_mapping(self.notes))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "target_family": self.target_family,
            "topology_class": self.topology_class,
            "export_modes": list(self.export_modes),
            "preserved_semantics": list(self.preserved_semantics),
            "lost_semantics": list(self.lost_semantics),
            "shadow_assumptions_required": self.shadow_assumptions_required,
            "notes": dict(self.notes),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ReplayExportIntegrityPacket":
        return ReplayExportIntegrityPacket(
            packet_id=data.get("packet_id") or "",
            target_family=data.get("target_family") or "",
            topology_class=data.get("topology_class") or "",
            export_modes=list(data.get("export_modes") or []),
            preserved_semantics=list(data.get("preserved_semantics") or []),
            lost_semantics=list(data.get("lost_semantics") or []),
            shadow_assumptions_required=bool(data.get("shadow_assumptions_required")),
            notes=dict(data.get("notes") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class BenchmarkControlPacket:
    packet_id: str
    target_family: str
    control_kind: str
    evaluator_stack: List[str]
    controls: List[str]
    known_confound_risks: List[str]
    notes: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "packet_id", _require_text(self.packet_id, "packet_id"))
        object.__setattr__(self, "target_family", _require_text(self.target_family, "target_family"))
        object.__setattr__(self, "control_kind", _require_text(self.control_kind, "control_kind"))
        object.__setattr__(self, "evaluator_stack", _copy_text_list(self.evaluator_stack))
        object.__setattr__(self, "controls", _copy_text_list(self.controls))
        object.__setattr__(self, "known_confound_risks", _copy_text_list(self.known_confound_risks))
        if not self.evaluator_stack:
            raise ValueError("evaluator_stack must contain at least one evaluator")
        object.__setattr__(self, "notes", _copy_mapping(self.notes))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "target_family": self.target_family,
            "control_kind": self.control_kind,
            "evaluator_stack": list(self.evaluator_stack),
            "controls": list(self.controls),
            "known_confound_risks": list(self.known_confound_risks),
            "notes": dict(self.notes),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "BenchmarkControlPacket":
        return BenchmarkControlPacket(
            packet_id=data.get("packet_id") or "",
            target_family=data.get("target_family") or "",
            control_kind=data.get("control_kind") or "",
            evaluator_stack=list(data.get("evaluator_stack") or []),
            controls=list(data.get("controls") or []),
            known_confound_risks=list(data.get("known_confound_risks") or []),
            notes=dict(data.get("notes") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ConsumerHandoffPacket:
    packet_id: str
    target_family: str
    consumer_kind: str
    artifact_kinds: List[str]
    handoff_contract: List[str]
    shadow_semantics_required: bool
    notes: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "packet_id", _require_text(self.packet_id, "packet_id"))
        object.__setattr__(self, "target_family", _require_text(self.target_family, "target_family"))
        object.__setattr__(self, "consumer_kind", _require_text(self.consumer_kind, "consumer_kind"))
        object.__setattr__(self, "artifact_kinds", _copy_text_list(self.artifact_kinds))
        object.__setattr__(self, "handoff_contract", _copy_text_list(self.handoff_contract))
        if not self.artifact_kinds:
            raise ValueError("artifact_kinds must contain at least one artifact kind")
        object.__setattr__(self, "shadow_semantics_required", bool(self.shadow_semantics_required))
        object.__setattr__(self, "notes", _copy_mapping(self.notes))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "target_family": self.target_family,
            "consumer_kind": self.consumer_kind,
            "artifact_kinds": list(self.artifact_kinds),
            "handoff_contract": list(self.handoff_contract),
            "shadow_semantics_required": self.shadow_semantics_required,
            "notes": dict(self.notes),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ConsumerHandoffPacket":
        return ConsumerHandoffPacket(
            packet_id=data.get("packet_id") or "",
            target_family=data.get("target_family") or "",
            consumer_kind=data.get("consumer_kind") or "",
            artifact_kinds=list(data.get("artifact_kinds") or []),
            handoff_contract=list(data.get("handoff_contract") or []),
            shadow_semantics_required=bool(data.get("shadow_semantics_required")),
            notes=dict(data.get("notes") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class CompositionSeamPacket:
    packet_id: str
    source_family: str
    target_kind: str
    seam_labels: List[str]
    issues: List[Dict[str, Any]]
    repeated_shape_candidate: bool
    notes: Dict[str, Any] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "packet_id", _require_text(self.packet_id, "packet_id"))
        object.__setattr__(self, "source_family", _require_text(self.source_family, "source_family"))
        object.__setattr__(self, "target_kind", _require_text(self.target_kind, "target_kind"))
        object.__setattr__(self, "seam_labels", _copy_text_list(self.seam_labels))
        if not self.seam_labels:
            raise ValueError("seam_labels must contain at least one seam label")
        object.__setattr__(self, "issues", _copy_mapping_list(self.issues))
        object.__setattr__(self, "repeated_shape_candidate", bool(self.repeated_shape_candidate))
        object.__setattr__(self, "notes", _copy_mapping(self.notes))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "packet_id": self.packet_id,
            "source_family": self.source_family,
            "target_kind": self.target_kind,
            "seam_labels": list(self.seam_labels),
            "issues": [dict(item) for item in self.issues],
            "repeated_shape_candidate": self.repeated_shape_candidate,
            "notes": dict(self.notes),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "CompositionSeamPacket":
        return CompositionSeamPacket(
            packet_id=data.get("packet_id") or "",
            source_family=data.get("source_family") or "",
            target_kind=data.get("target_kind") or "",
            seam_labels=list(data.get("seam_labels") or []),
            issues=[dict(item) for item in data.get("issues") or []],
            repeated_shape_candidate=bool(data.get("repeated_shape_candidate")),
            notes=dict(data.get("notes") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


def _score(candidate: SearchCandidate) -> float:
    return float(candidate.score_vector.get("correctness_score", 0.0))


def compute_fidelity_metrics(run: SearchRun) -> Dict[str, float]:
    initial_candidates = [item for item in run.candidates if item.round_index == 0]
    final_frontier = max(run.frontiers, key=lambda item: item.round_index)
    final_candidates = [item for item in run.candidates if item.frontier_id == final_frontier.frontier_id]
    selected = next((item for item in run.candidates if item.candidate_id == run.selected_candidate_id), None)
    assessments = list(run.assessments)
    pass_count = sum(1 for item in assessments if item.verdict == "pass")
    initial_best = max((_score(item) for item in initial_candidates), default=0.0)
    final_best = max((_score(item) for item in final_candidates), default=0.0)
    selected_score = _score(selected) if selected is not None else final_best
    message_count = max(len(run.messages), 1)
    return {
        "aggregability_gap": float(run.metrics.aggregability_gap if run.metrics is not None else 0.0),
        "diversity_mixing": float(run.metrics.mixing_rate if run.metrics is not None else 0.0),
        "aggregation_gain": max(0.0, final_best - initial_best),
        "emergent_correctness": max(0.0, selected_score - initial_best),
        "message_efficiency": selected_score / float(message_count),
        "verifier_yield": pass_count / float(max(len(assessments), 1)),
    }


def build_default_fidelity_scorecard(
    *,
    scorecard_id: str,
    paper_key: str,
    fidelity_label: str,
    structural_fidelity: str,
    evaluator_fidelity: str,
    compute_fidelity: str,
    training_aware_fidelity: str,
    notes: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> FidelityScorecard:
    return FidelityScorecard(
        scorecard_id=scorecard_id,
        paper_key=paper_key,
        fidelity_label=fidelity_label,
        dimensions={
            "structural_fidelity": structural_fidelity,
            "evaluator_fidelity": evaluator_fidelity,
            "compute_fidelity": compute_fidelity,
            "training_aware_fidelity": training_aware_fidelity,
        },
        notes=dict(notes or {}),
        metadata=dict(metadata or {}),
    )
