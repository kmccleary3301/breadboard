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
