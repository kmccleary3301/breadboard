from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Optional, Sequence


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
class EnvironmentSelector:
    selector_id: str
    environment_kind: str
    profile: str
    required_capabilities: List[str] = field(default_factory=list)
    forbidden_capabilities: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "selector_id", _require_text(self.selector_id, "selector_id"))
        object.__setattr__(self, "environment_kind", _require_text(self.environment_kind, "environment_kind"))
        object.__setattr__(self, "profile", _require_text(self.profile, "profile"))
        object.__setattr__(self, "required_capabilities", _copy_text_list(self.required_capabilities))
        object.__setattr__(self, "forbidden_capabilities", _copy_text_list(self.forbidden_capabilities))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "selector_id": self.selector_id,
            "environment_kind": self.environment_kind,
            "profile": self.profile,
            "required_capabilities": list(self.required_capabilities),
            "forbidden_capabilities": list(self.forbidden_capabilities),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "EnvironmentSelector":
        return EnvironmentSelector(
            selector_id=data.get("selector_id") or data.get("id") or "",
            environment_kind=data.get("environment_kind") or "",
            profile=data.get("profile") or "",
            required_capabilities=list(data.get("required_capabilities") or []),
            forbidden_capabilities=list(data.get("forbidden_capabilities") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ToolRequirement:
    tool_name: str
    access_mode: str = "required"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "tool_name", _require_text(self.tool_name, "tool_name"))
        access_mode = _require_text(self.access_mode, "access_mode")
        if access_mode not in {"required", "optional"}:
            raise ValueError("access_mode must be one of: required, optional")
        object.__setattr__(self, "access_mode", access_mode)
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "access_mode": self.access_mode,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ToolRequirement":
        return ToolRequirement(
            tool_name=data.get("tool_name") or data.get("name") or "",
            access_mode=data.get("access_mode") or "required",
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ToolPackContext:
    pack_id: str
    profile: str
    required_tools: List[ToolRequirement] = field(default_factory=list)
    optional_tools: List[ToolRequirement] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "pack_id", _require_text(self.pack_id, "pack_id"))
        object.__setattr__(self, "profile", _require_text(self.profile, "profile"))
        object.__setattr__(
            self,
            "required_tools",
            [item if isinstance(item, ToolRequirement) else ToolRequirement.from_dict(item) for item in self.required_tools],
        )
        object.__setattr__(
            self,
            "optional_tools",
            [item if isinstance(item, ToolRequirement) else ToolRequirement.from_dict(item) for item in self.optional_tools],
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))
        names = [item.tool_name for item in self.required_tools + self.optional_tools]
        if len(names) != len(set(names)):
            raise ValueError("tool pack contains duplicate tool names")

    def required_tool_names(self) -> List[str]:
        return [item.tool_name for item in self.required_tools]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pack_id": self.pack_id,
            "profile": self.profile,
            "required_tools": [item.to_dict() for item in self.required_tools],
            "optional_tools": [item.to_dict() for item in self.optional_tools],
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ToolPackContext":
        return ToolPackContext(
            pack_id=data.get("pack_id") or data.get("id") or "",
            profile=data.get("profile") or "",
            required_tools=[ToolRequirement.from_dict(item) for item in data.get("required_tools") or []],
            optional_tools=[ToolRequirement.from_dict(item) for item in data.get("optional_tools") or []],
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class ServiceContextRequirement:
    service_id: str
    selector: str
    requires_network_access: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "service_id", _require_text(self.service_id, "service_id"))
        object.__setattr__(self, "selector", _require_text(self.selector, "selector"))
        object.__setattr__(self, "requires_network_access", bool(self.requires_network_access))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "service_id": self.service_id,
            "selector": self.selector,
            "requires_network_access": self.requires_network_access,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "ServiceContextRequirement":
        return ServiceContextRequirement(
            service_id=data.get("service_id") or data.get("id") or "",
            selector=data.get("selector") or "",
            requires_network_access=bool(data.get("requires_network_access")),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class SandboxContextRequirement:
    sandbox_profile: str
    filesystem_mode: str
    network_access: bool = False
    required_capabilities: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "sandbox_profile", _require_text(self.sandbox_profile, "sandbox_profile"))
        object.__setattr__(self, "filesystem_mode", _require_text(self.filesystem_mode, "filesystem_mode"))
        object.__setattr__(self, "network_access", bool(self.network_access))
        object.__setattr__(self, "required_capabilities", _copy_text_list(self.required_capabilities))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sandbox_profile": self.sandbox_profile,
            "filesystem_mode": self.filesystem_mode,
            "network_access": self.network_access,
            "required_capabilities": list(self.required_capabilities),
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "SandboxContextRequirement":
        return SandboxContextRequirement(
            sandbox_profile=data.get("sandbox_profile") or data.get("profile") or "",
            filesystem_mode=data.get("filesystem_mode") or "",
            network_access=bool(data.get("network_access")),
            required_capabilities=list(data.get("required_capabilities") or []),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class MCPContextRequirement:
    server_names: List[str] = field(default_factory=list)
    required_resources: List[str] = field(default_factory=list)
    requires_network_access: bool = False
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "server_names", _copy_text_list(self.server_names))
        object.__setattr__(self, "required_resources", _copy_text_list(self.required_resources))
        object.__setattr__(self, "requires_network_access", bool(self.requires_network_access))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))
        if self.required_resources and not self.server_names:
            raise ValueError("server_names must be non-empty when required_resources are declared")

    def to_dict(self) -> Dict[str, Any]:
        return {
            "server_names": list(self.server_names),
            "required_resources": list(self.required_resources),
            "requires_network_access": self.requires_network_access,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "MCPContextRequirement":
        return MCPContextRequirement(
            server_names=list(data.get("server_names") or []),
            required_resources=list(data.get("required_resources") or []),
            requires_network_access=bool(data.get("requires_network_access")),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class OptimizationRuntimeContext:
    context_id: str
    environment_selector: EnvironmentSelector
    tool_pack_context: ToolPackContext
    service_contexts: List[ServiceContextRequirement] = field(default_factory=list)
    sandbox_context: Optional[SandboxContextRequirement] = None
    mcp_context: Optional[MCPContextRequirement] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "context_id", _require_text(self.context_id, "context_id"))
        object.__setattr__(
            self,
            "environment_selector",
            self.environment_selector
            if isinstance(self.environment_selector, EnvironmentSelector)
            else EnvironmentSelector.from_dict(self.environment_selector),
        )
        object.__setattr__(
            self,
            "tool_pack_context",
            self.tool_pack_context
            if isinstance(self.tool_pack_context, ToolPackContext)
            else ToolPackContext.from_dict(self.tool_pack_context),
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
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))
        if self.tool_pack_context.required_tools and not self.environment_selector.profile:
            raise ValueError("environment selector profile must be non-empty when tool-pack requirements are declared")
        sandbox = self.sandbox_context
        if sandbox is not None:
            if any(service.requires_network_access for service in self.service_contexts) and not sandbox.network_access:
                raise ValueError("service context requires network access but sandbox_context.network_access is false")
            if self.mcp_context and self.mcp_context.requires_network_access and not sandbox.network_access:
                raise ValueError("mcp context requires network access but sandbox_context.network_access is false")

    def required_tool_names(self) -> List[str]:
        return self.tool_pack_context.required_tool_names()

    def to_dict(self) -> Dict[str, Any]:
        payload = {
            "context_id": self.context_id,
            "environment_selector": self.environment_selector.to_dict(),
            "tool_pack_context": self.tool_pack_context.to_dict(),
            "service_contexts": [item.to_dict() for item in self.service_contexts],
            "metadata": dict(self.metadata),
        }
        if self.sandbox_context is not None:
            payload["sandbox_context"] = self.sandbox_context.to_dict()
        if self.mcp_context is not None:
            payload["mcp_context"] = self.mcp_context.to_dict()
        return payload

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "OptimizationRuntimeContext":
        return OptimizationRuntimeContext(
            context_id=data.get("context_id") or data.get("id") or "",
            environment_selector=EnvironmentSelector.from_dict(data.get("environment_selector") or {}),
            tool_pack_context=ToolPackContext.from_dict(data.get("tool_pack_context") or {}),
            service_contexts=[ServiceContextRequirement.from_dict(item) for item in data.get("service_contexts") or []],
            sandbox_context=None
            if not data.get("sandbox_context")
            else SandboxContextRequirement.from_dict(data.get("sandbox_context") or {}),
            mcp_context=None
            if not data.get("mcp_context")
            else MCPContextRequirement.from_dict(data.get("mcp_context") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class RuntimeCompatibilityIssue:
    issue_id: str
    category: str
    message: str
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "issue_id", _require_text(self.issue_id, "issue_id"))
        object.__setattr__(self, "category", _require_text(self.category, "category"))
        object.__setattr__(self, "message", _require_text(self.message, "message"))
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "issue_id": self.issue_id,
            "category": self.category,
            "message": self.message,
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "RuntimeCompatibilityIssue":
        return RuntimeCompatibilityIssue(
            issue_id=data.get("issue_id") or data.get("id") or "",
            category=data.get("category") or "",
            message=data.get("message") or "",
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(frozen=True)
class RuntimeCompatibilityResult:
    context_id: str
    target_id: str
    candidate_id: str
    status: str
    issues: List[RuntimeCompatibilityIssue] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "context_id", _require_text(self.context_id, "context_id"))
        object.__setattr__(self, "target_id", _require_text(self.target_id, "target_id"))
        object.__setattr__(self, "candidate_id", _require_text(self.candidate_id, "candidate_id"))
        status = _require_text(self.status, "status")
        if status not in {"compatible", "incompatible"}:
            raise ValueError("status must be one of: compatible, incompatible")
        object.__setattr__(self, "status", status)
        object.__setattr__(
            self,
            "issues",
            [item if isinstance(item, RuntimeCompatibilityIssue) else RuntimeCompatibilityIssue.from_dict(item) for item in self.issues],
        )
        object.__setattr__(self, "metadata", _copy_mapping(self.metadata))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "context_id": self.context_id,
            "target_id": self.target_id,
            "candidate_id": self.candidate_id,
            "status": self.status,
            "issues": [item.to_dict() for item in self.issues],
            "metadata": dict(self.metadata),
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "RuntimeCompatibilityResult":
        return RuntimeCompatibilityResult(
            context_id=data.get("context_id") or "",
            target_id=data.get("target_id") or "",
            candidate_id=data.get("candidate_id") or "",
            status=data.get("status") or "",
            issues=[RuntimeCompatibilityIssue.from_dict(item) for item in data.get("issues") or []],
            metadata=dict(data.get("metadata") or {}),
        )
