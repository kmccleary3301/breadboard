"""Versioned public semantic-controller interface map.

The map names the existing provider and runner contracts at the boundary.  It
is deliberately data-only: policy implementations choose messages, while the
controller owns ordering and terminal lifecycle and tool/workspace adapters
own their respective effects.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


SEMANTIC_INTERFACE_MAP_SCHEMA_VERSION = "bb.rl.semantic-interface-map.v1"

SemanticInterfaceOwner = Literal[
    "breadboard_controller",
    "external_ual",
    "provider_runtime",
    "tool_runtime",
    "workspace",
]


@dataclass(frozen=True, slots=True)
class SemanticInterfaceBinding:
    """One public contract and the boundary that owns its lifecycle."""

    name: str
    owner: SemanticInterfaceOwner
    symbol: str

    def __post_init__(self) -> None:
        if (
            type(self.name) is not str
            or not self.name
            or self.name != self.name.strip()
        ):
            raise ValueError("semantic interface name must be a nonempty identifier")
        if (
            type(self.symbol) is not str
            or not self.symbol
            or self.symbol != self.symbol.strip()
        ):
            raise ValueError("semantic interface symbol must be a nonempty name")
        if self.owner not in {
            "breadboard_controller",
            "external_ual",
            "provider_runtime",
            "tool_runtime",
            "workspace",
        }:
            raise ValueError("semantic interface owner is unsupported")

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "owner": self.owner, "symbol": self.symbol}


@dataclass(frozen=True, slots=True)
class SemanticInterfaceMap:
    """Stable map of semantic roles to existing public Python contracts."""

    schema_version: Literal["bb.rl.semantic-interface-map.v1"]
    interfaces: tuple[SemanticInterfaceBinding, ...]
    external_ual_ownership: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.schema_version != SEMANTIC_INTERFACE_MAP_SCHEMA_VERSION:
            raise ValueError("unsupported semantic interface-map schema")
        interfaces = tuple(self.interfaces)
        if not interfaces:
            raise ValueError("semantic interface map must not be empty")
        if any(type(item) is not SemanticInterfaceBinding for item in interfaces):
            raise TypeError("semantic interface map contains an invalid binding")
        names = tuple(item.name for item in interfaces)
        if len(names) != len(set(names)):
            raise ValueError("semantic interface map names must be unique")
        object.__setattr__(self, "interfaces", interfaces)
        external_ual_ownership = tuple(self.external_ual_ownership)
        if (
            not external_ual_ownership
            or len(external_ual_ownership) != len(set(external_ual_ownership))
            or any(type(name) is not str or not name for name in external_ual_ownership)
        ):
            raise ValueError("external UAL ownership fields must be unique names")
        object.__setattr__(
            self,
            "external_ual_ownership",
            external_ual_ownership,
        )

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible map document."""
        return {
            "schema_version": self.schema_version,
            "interfaces": [item.as_dict() for item in self.interfaces],
            "external_ual_ownership": list(self.external_ual_ownership),
        }

    def binding(self, name: str) -> SemanticInterfaceBinding:
        """Look up a named boundary contract without exposing internal storage."""
        for item in self.interfaces:
            if item.name == name:
                return item
        raise KeyError(name)


EXTERNAL_UAL_OWNERSHIP = (
    "sampled_token_history",
    "behavior_logprobs",
    "loss_masks",
    "behavior_policy_identity",
    "training_admission",
    "trajectory_join",
)


SEMANTIC_INTERFACE_MAP = SemanticInterfaceMap(
    schema_version=SEMANTIC_INTERFACE_MAP_SCHEMA_VERSION,
    external_ual_ownership=EXTERNAL_UAL_OWNERSHIP,
    interfaces=(
        SemanticInterfaceBinding(
            name="semantic_message",
            owner="external_ual",
            symbol="breadboard_engine.provider.contracts.ProviderMessage",
        ),
        SemanticInterfaceBinding(
            name="semantic_history",
            owner="breadboard_controller",
            symbol="breadboard_engine.provider.contracts.ProviderRequest",
        ),
        SemanticInterfaceBinding(
            name="tool_call",
            owner="external_ual",
            symbol="breadboard_engine.provider.contracts.ProviderToolCall",
        ),
        SemanticInterfaceBinding(
            name="tool_result",
            owner="tool_runtime",
            symbol="breadboard_engine.provider.contracts.normalize_tool_result_dict",
        ),
        SemanticInterfaceBinding(
            name="provider_event",
            owner="provider_runtime",
            symbol="breadboard_engine.provider.contracts.ProviderEvent",
        ),
        SemanticInterfaceBinding(
            name="provider_exchange",
            owner="breadboard_controller",
            symbol="breadboard_engine.provider.contracts.ProviderExchangeV2",
        ),
        SemanticInterfaceBinding(
            name="policy_runtime",
            owner="external_ual",
            symbol="breadboard.rl.harness.runners.base.PolicyRuntimeClientPort",
        ),
        SemanticInterfaceBinding(
            name="tool_runtime",
            owner="tool_runtime",
            symbol="breadboard.rl.harness.runners.base.ConductorToolPort",
        ),
        SemanticInterfaceBinding(
            name="workspace_runtime",
            owner="workspace",
            symbol="breadboard.rl.harness.runners.base.RunnerWorkspacePort",
        ),
        SemanticInterfaceBinding(
            name="runner_lifecycle",
            owner="breadboard_controller",
            symbol="breadboard.rl.harness.runners.base.RunnerResult",
        ),
    ),
)


__all__ = [
    "EXTERNAL_UAL_OWNERSHIP",
    "SEMANTIC_INTERFACE_MAP",
    "SEMANTIC_INTERFACE_MAP_SCHEMA_VERSION",
    "SemanticInterfaceBinding",
    "SemanticInterfaceMap",
    "SemanticInterfaceOwner",
]
