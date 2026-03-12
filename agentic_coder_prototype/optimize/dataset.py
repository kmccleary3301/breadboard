from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .context import (
    EnvironmentSelector,
    MCPContextRequirement,
    OptimizationRuntimeContext,
    SandboxContextRequirement,
    ServiceContextRequirement,
    ToolPackContext,
    ToolRequirement,
)
from .substrate import ArtifactRef


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
class CorrectnessRationale:
    rationale_id: str
    summary: str
    explanation: str
    evidence_refs: List[ArtifactRef] = field(default_factory=list)
    acceptance_clauses: List[str] = field(default_factory=list)
    forbidden_behavior_clauses: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "rationale_id", _require_text(self.rationale_id, "rationale_id"))
        object.__setattr__(self, "summary", _require_text(self.summary, "summary"))
        object.__setattr__(self, "explanation", _require_text(self.explanation, "explanation"))
        object.__setattr__(
            self,
            "evidence_refs",
            [item if isinstance(item, ArtifactRef) else ArtifactRef.from_dict(item) for item in self.evidence_refs],
        )
        object.__setattr__(self, "acceptance_clauses", _copy_text_list(self.acceptance_clauses))
        object.__setattr__(self, "forbidden_behavior_clauses", _copy_text_list(self.forbidden_behavior_clauses))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "rationale_id": self.rationale_id,
            "summary": self.summary,
            "explanation": self.explanation,
            "evidence_refs": [item.to_dict() for item in self.evidence_refs],
            "acceptance_clauses": list(self.acceptance_clauses),
            "forbidden_behavior_clauses": list(self.forbidden_behavior_clauses),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "CorrectnessRationale":
        return CorrectnessRationale(
            rationale_id=data.get("rationale_id") or data.get("id") or "",
            summary=data.get("summary") or "",
            explanation=data.get("explanation") or "",
            evidence_refs=[ArtifactRef.from_dict(item) for item in data.get("evidence_refs") or []],
            acceptance_clauses=list(data.get("acceptance_clauses") or []),
            forbidden_behavior_clauses=list(data.get("forbidden_behavior_clauses") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class GroundTruthPackage:
    package_id: str
    oracle_kind: str
    expected_result: Dict[str, Any] = field(default_factory=dict)
    expected_artifacts: List[ArtifactRef] = field(default_factory=list)
    rationale_refs: List[str] = field(default_factory=list)
    executable_checks: List[Dict[str, Any]] = field(default_factory=list)
    admissible_alternatives: List[Dict[str, Any]] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "package_id", _require_text(self.package_id, "package_id"))
        object.__setattr__(self, "oracle_kind", _require_text(self.oracle_kind, "oracle_kind"))
        object.__setattr__(self, "expected_result", _copy_mapping(self.expected_result))
        object.__setattr__(
            self,
            "expected_artifacts",
            [item if isinstance(item, ArtifactRef) else ArtifactRef.from_dict(item) for item in self.expected_artifacts],
        )
        object.__setattr__(self, "rationale_refs", _copy_text_list(self.rationale_refs))
        object.__setattr__(self, "executable_checks", [dict(item) for item in self.executable_checks or []])
        object.__setattr__(self, "admissible_alternatives", [dict(item) for item in self.admissible_alternatives or []])
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))
        if not self.expected_result and not self.expected_artifacts and not self.executable_checks:
            raise ValueError("ground truth package must include expected_result, expected_artifacts, or executable_checks")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "package_id": self.package_id,
            "oracle_kind": self.oracle_kind,
            "expected_result": dict(self.expected_result),
            "expected_artifacts": [item.to_dict() for item in self.expected_artifacts],
            "rationale_refs": list(self.rationale_refs),
            "executable_checks": [dict(item) for item in self.executable_checks],
            "admissible_alternatives": [dict(item) for item in self.admissible_alternatives],
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "GroundTruthPackage":
        return GroundTruthPackage(
            package_id=data.get("package_id") or data.get("id") or "",
            oracle_kind=data.get("oracle_kind") or "",
            expected_result=dict(data.get("expected_result") or {}),
            expected_artifacts=[ArtifactRef.from_dict(item) for item in data.get("expected_artifacts") or []],
            rationale_refs=list(data.get("rationale_refs") or []),
            executable_checks=[dict(item) for item in data.get("executable_checks") or []],
            admissible_alternatives=[dict(item) for item in data.get("admissible_alternatives") or []],
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class OptimizationSample:
    sample_id: str
    target_id: str
    prompt_input: str
    environment_requirements: Dict[str, Any] = field(default_factory=dict)
    bound_tool_requirements: List[str] = field(default_factory=list)
    environment_selector: Optional[EnvironmentSelector] = None
    tool_pack_context: Optional[ToolPackContext] = None
    service_contexts: List[ServiceContextRequirement] = field(default_factory=list)
    sandbox_context: Optional[SandboxContextRequirement] = None
    mcp_context: Optional[MCPContextRequirement] = None
    expected_checks: List[str] = field(default_factory=list)
    ground_truth_package: GroundTruthPackage = field(default_factory=lambda: GroundTruthPackage(package_id="placeholder", oracle_kind="placeholder", expected_result={"placeholder": True}))
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "sample_id", _require_text(self.sample_id, "sample_id"))
        object.__setattr__(self, "target_id", _require_text(self.target_id, "target_id"))
        object.__setattr__(self, "prompt_input", _require_text(self.prompt_input, "prompt_input"))
        object.__setattr__(self, "environment_requirements", _copy_mapping(self.environment_requirements))
        object.__setattr__(self, "bound_tool_requirements", _copy_text_list(self.bound_tool_requirements))
        object.__setattr__(
            self,
            "environment_selector",
            None
            if self.environment_selector is None
            else (
                self.environment_selector
                if isinstance(self.environment_selector, EnvironmentSelector)
                else EnvironmentSelector.from_dict(self.environment_selector)
            ),
        )
        object.__setattr__(
            self,
            "tool_pack_context",
            None
            if self.tool_pack_context is None
            else (
                self.tool_pack_context
                if isinstance(self.tool_pack_context, ToolPackContext)
                else ToolPackContext.from_dict(self.tool_pack_context)
            ),
        )
        object.__setattr__(
            self,
            "service_contexts",
            [item if isinstance(item, ServiceContextRequirement) else ServiceContextRequirement.from_dict(item) for item in self.service_contexts],
        )
        object.__setattr__(
            self,
            "sandbox_context",
            None
            if self.sandbox_context is None
            else (
                self.sandbox_context
                if isinstance(self.sandbox_context, SandboxContextRequirement)
                else SandboxContextRequirement.from_dict(self.sandbox_context)
            ),
        )
        object.__setattr__(
            self,
            "mcp_context",
            None
            if self.mcp_context is None
            else (
                self.mcp_context
                if isinstance(self.mcp_context, MCPContextRequirement)
                else MCPContextRequirement.from_dict(self.mcp_context)
            ),
        )
        object.__setattr__(self, "expected_checks", _copy_text_list(self.expected_checks))
        object.__setattr__(
            self,
            "ground_truth_package",
            self.ground_truth_package if isinstance(self.ground_truth_package, GroundTruthPackage) else GroundTruthPackage.from_dict(self.ground_truth_package),
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))
        self.runtime_context()

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "sample_id": self.sample_id,
            "target_id": self.target_id,
            "prompt_input": self.prompt_input,
            "environment_requirements": dict(self.environment_requirements),
            "bound_tool_requirements": list(self.bound_tool_requirements),
            "expected_checks": list(self.expected_checks),
            "ground_truth_package": self.ground_truth_package.to_dict(),
            "metadata": dict(self.metadata),
        }
        if self.environment_selector is not None:
            payload["environment_selector"] = self.environment_selector.to_dict()
        if self.tool_pack_context is not None:
            payload["tool_pack_context"] = self.tool_pack_context.to_dict()
        if self.service_contexts:
            payload["service_contexts"] = [item.to_dict() for item in self.service_contexts]
        if self.sandbox_context is not None:
            payload["sandbox_context"] = self.sandbox_context.to_dict()
        if self.mcp_context is not None:
            payload["mcp_context"] = self.mcp_context.to_dict()
        return payload

    def runtime_context(self) -> OptimizationRuntimeContext:
        environment_selector = self.environment_selector
        if environment_selector is None:
            workspace_mode = str(self.environment_requirements.get("workspace_mode") or self.environment_requirements.get("environment_kind") or "workspace-write")
            environment_selector = EnvironmentSelector(
                selector_id=f"env.{self.sample_id}",
                environment_kind=workspace_mode,
                profile=workspace_mode,
                required_capabilities=_copy_text_list(self.environment_requirements.get("required_capabilities") or [workspace_mode]),
                forbidden_capabilities=_copy_text_list(self.environment_requirements.get("forbidden_capabilities") or []),
                metadata={"source": "legacy_environment_requirements"},
            )
        tool_pack_context = self.tool_pack_context
        if tool_pack_context is None:
            tool_pack_context = ToolPackContext(
                pack_id=f"toolpack.{self.sample_id}",
                profile=str(self.environment_requirements.get("tool_pack_profile") or "legacy_bound_tools"),
                required_tools=[ToolRequirement(tool_name=name) for name in self.bound_tool_requirements],
                optional_tools=[],
                metadata={"source": "legacy_bound_tool_requirements"},
            )
        sandbox_context = self.sandbox_context
        if sandbox_context is None:
            sandbox_context = SandboxContextRequirement(
                sandbox_profile=str(self.environment_requirements.get("sandbox_profile") or "workspace-default"),
                filesystem_mode=str(self.environment_requirements.get("workspace_mode") or "workspace-write"),
                network_access=bool(self.environment_requirements.get("network_access")),
                required_capabilities=_copy_text_list(self.environment_requirements.get("sandbox_capabilities") or []),
                metadata={"source": "legacy_environment_requirements"},
            )
        mcp_context = self.mcp_context
        if mcp_context is None:
            mcp_context = MCPContextRequirement(
                server_names=_copy_text_list(self.environment_requirements.get("mcp_servers") or []),
                required_resources=_copy_text_list(self.environment_requirements.get("mcp_resources") or []),
                requires_network_access=bool(self.environment_requirements.get("mcp_requires_network_access")),
                metadata={"source": "legacy_environment_requirements"},
            )
        service_contexts = self.service_contexts
        if not service_contexts:
            inferred_services = self.environment_requirements.get("service_selectors") or {}
            service_contexts = [
                ServiceContextRequirement(
                    service_id=str(service_id),
                    selector=str(selector),
                    requires_network_access=bool(self.environment_requirements.get("network_access")),
                    metadata={"source": "legacy_environment_requirements"},
                )
                for service_id, selector in dict(inferred_services).items()
            ]
        return OptimizationRuntimeContext(
            context_id=f"context.{self.sample_id}",
            environment_selector=environment_selector,
            tool_pack_context=tool_pack_context,
            service_contexts=service_contexts,
            sandbox_context=sandbox_context,
            mcp_context=mcp_context,
            metadata={"sample_id": self.sample_id},
        )

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "OptimizationSample":
        return OptimizationSample(
            sample_id=data.get("sample_id") or data.get("id") or "",
            target_id=data.get("target_id") or "",
            prompt_input=data.get("prompt_input") or data.get("task_input") or "",
            environment_requirements=dict(data.get("environment_requirements") or {}),
            bound_tool_requirements=list(data.get("bound_tool_requirements") or []),
            environment_selector=None
            if not data.get("environment_selector")
            else EnvironmentSelector.from_dict(data.get("environment_selector") or {}),
            tool_pack_context=None
            if not data.get("tool_pack_context")
            else ToolPackContext.from_dict(data.get("tool_pack_context") or {}),
            service_contexts=[ServiceContextRequirement.from_dict(item) for item in data.get("service_contexts") or []],
            sandbox_context=None
            if not data.get("sandbox_context")
            else SandboxContextRequirement.from_dict(data.get("sandbox_context") or {}),
            mcp_context=None
            if not data.get("mcp_context")
            else MCPContextRequirement.from_dict(data.get("mcp_context") or {}),
            expected_checks=list(data.get("expected_checks") or []),
            ground_truth_package=GroundTruthPackage.from_dict(data.get("ground_truth_package") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class OptimizationDataset:
    dataset_id: str
    dataset_version: str
    samples: List[OptimizationSample] = field(default_factory=list)
    split_definitions: Dict[str, List[str]] = field(default_factory=dict)
    scope_notes: List[str] = field(default_factory=list)
    reproducibility_metadata: Dict[str, Any] = field(default_factory=dict)
    dataset_runtime_context: Optional[OptimizationRuntimeContext] = None
    rationale_catalog: List[CorrectnessRationale] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "dataset_id", _require_text(self.dataset_id, "dataset_id"))
        object.__setattr__(self, "dataset_version", _require_text(self.dataset_version, "dataset_version"))
        object.__setattr__(
            self,
            "samples",
            [item if isinstance(item, OptimizationSample) else OptimizationSample.from_dict(item) for item in self.samples],
        )
        object.__setattr__(self, "split_definitions", {str(k): _copy_text_list(v) for k, v in dict(self.split_definitions or {}).items()})
        object.__setattr__(self, "scope_notes", _copy_text_list(self.scope_notes))
        object.__setattr__(self, "reproducibility_metadata", _copy_mapping(self.reproducibility_metadata))
        object.__setattr__(
            self,
            "dataset_runtime_context",
            None
            if self.dataset_runtime_context is None
            else (
                self.dataset_runtime_context
                if isinstance(self.dataset_runtime_context, OptimizationRuntimeContext)
                else OptimizationRuntimeContext.from_dict(self.dataset_runtime_context)
            ),
        )
        object.__setattr__(
            self,
            "rationale_catalog",
            [item if isinstance(item, CorrectnessRationale) else CorrectnessRationale.from_dict(item) for item in self.rationale_catalog],
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

        if not self.samples:
            raise ValueError("samples must contain at least one optimization sample")

        sample_ids = [sample.sample_id for sample in self.samples]
        if len(sample_ids) != len(set(sample_ids)):
            raise ValueError("samples contains duplicate sample_id values")

        rationale_ids = [item.rationale_id for item in self.rationale_catalog]
        if len(rationale_ids) != len(set(rationale_ids)):
            raise ValueError("rationale_catalog contains duplicate rationale_id values")

        available_rationales = set(rationale_ids)
        for sample in self.samples:
            missing = sorted(set(sample.ground_truth_package.rationale_refs) - available_rationales)
            if missing:
                raise ValueError(f"sample {sample.sample_id} references unknown rationales: {missing}")

        if self.split_definitions:
            declared = set()
            for sample_ids_for_split in self.split_definitions.values():
                declared.update(sample_ids_for_split)
            unknown = sorted(declared - set(sample_ids))
            if unknown:
                raise ValueError(f"split_definitions references unknown sample ids: {unknown}")

    def sample_ids(self) -> List[str]:
        return [sample.sample_id for sample in self.samples]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_version": self.dataset_version,
            "samples": [sample.to_dict() for sample in self.samples],
            "split_definitions": {key: list(value) for key, value in self.split_definitions.items()},
            "scope_notes": list(self.scope_notes),
            "reproducibility_metadata": dict(self.reproducibility_metadata),
            "dataset_runtime_context": None if self.dataset_runtime_context is None else self.dataset_runtime_context.to_dict(),
            "rationale_catalog": [item.to_dict() for item in self.rationale_catalog],
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "OptimizationDataset":
        return OptimizationDataset(
            dataset_id=data.get("dataset_id") or data.get("id") or "",
            dataset_version=data.get("dataset_version") or data.get("version") or "",
            samples=[OptimizationSample.from_dict(item) for item in data.get("samples") or []],
            split_definitions={str(k): list(v) for k, v in dict(data.get("split_definitions") or {}).items()},
            scope_notes=list(data.get("scope_notes") or []),
            reproducibility_metadata=dict(data.get("reproducibility_metadata") or {}),
            dataset_runtime_context=None
            if not data.get("dataset_runtime_context")
            else OptimizationRuntimeContext.from_dict(data.get("dataset_runtime_context") or {}),
            rationale_catalog=[CorrectnessRationale.from_dict(item) for item in data.get("rationale_catalog") or []],
            metadata=dict(data.get("metadata") or {}),
        )
