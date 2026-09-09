"""Module requests and separately issued admission grants."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum


def _text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value or value.strip() != value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _object(value: object, fields: frozenset[str], name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object")
    unknown = set(value) - fields
    if unknown:
        raise ValueError(f"{name} contains unknown fields: {', '.join(sorted(unknown))}")
    return value


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list, frozenset)):
        raise ValueError(f"{field} must be an array of strings")
    result = tuple(_text(item, field) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{field} must not contain duplicates")
    return result


class ProjectOperation(str, Enum):
    READ = "read"
    WRITE = "write"


class NetworkOperation(str, Enum):
    CONNECT = "connect"
    RESOLVE = "resolve"


@dataclass(frozen=True, slots=True)
class ProjectAuthority:
    roots: tuple[str, ...]
    operations: frozenset[ProjectOperation]

    def __post_init__(self) -> None:
        object.__setattr__(self, "roots", _strings(self.roots, "project roots"))
        object.__setattr__(self, "operations", frozenset(ProjectOperation(item) for item in self.operations))

    @classmethod
    def from_dict(cls, value: object) -> ProjectAuthority:
        raw = _object(value, frozenset({"roots", "operations"}), "project authority")
        return cls(
            _strings(raw.get("roots", ()), "project roots"),
            frozenset(ProjectOperation(item) for item in _strings(raw.get("operations", ()), "project operations")),
        )

    def to_dict(self) -> dict[str, object]:
        return {"roots": list(self.roots), "operations": sorted(item.value for item in self.operations)}


@dataclass(frozen=True, slots=True)
class NetworkAuthority:
    destinations: tuple[str, ...]
    operations: frozenset[NetworkOperation]

    def __post_init__(self) -> None:
        object.__setattr__(self, "destinations", _strings(self.destinations, "network destinations"))
        object.__setattr__(self, "operations", frozenset(NetworkOperation(item) for item in self.operations))

    @classmethod
    def from_dict(cls, value: object) -> NetworkAuthority:
        raw = _object(value, frozenset({"destinations", "operations"}), "network authority")
        return cls(
            _strings(raw.get("destinations", ()), "network destinations"),
            frozenset(NetworkOperation(item) for item in _strings(raw.get("operations", ()), "network operations")),
        )

    def to_dict(self) -> dict[str, object]:
        return {"destinations": list(self.destinations), "operations": sorted(item.value for item in self.operations)}


@dataclass(frozen=True, slots=True)
class ChildAuthority:
    allowed_module_ids: frozenset[str]
    max_depth: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "allowed_module_ids", frozenset(_strings(self.allowed_module_ids, "allowed module IDs")))
        if type(self.max_depth) is not int or self.max_depth < 0:
            raise ValueError("child max_depth must be a non-negative integer")

    @classmethod
    def from_dict(cls, value: object) -> ChildAuthority:
        raw = _object(value, frozenset({"allowed_module_ids", "max_depth"}), "child authority")
        return cls(frozenset(_strings(raw.get("allowed_module_ids", ()), "allowed module IDs")), raw.get("max_depth", 0))

    def to_dict(self) -> dict[str, object]:
        return {"allowed_module_ids": sorted(self.allowed_module_ids), "max_depth": self.max_depth}


@dataclass(frozen=True, slots=True)
class CredentialDisclosure:
    secret_name: str
    purpose: str

    def __post_init__(self) -> None:
        _text(self.secret_name, "secret name")
        _text(self.purpose, "credential disclosure purpose")

    @classmethod
    def from_dict(cls, value: object) -> CredentialDisclosure:
        raw = _object(value, frozenset({"secret_name", "purpose"}), "credential disclosure")
        return cls(_text(raw.get("secret_name"), "secret name"), _text(raw.get("purpose"), "credential disclosure purpose"))

    def to_dict(self) -> dict[str, str]:
        return {"secret_name": self.secret_name, "purpose": self.purpose}


@dataclass(frozen=True, slots=True)
class AuthorityDeclaration:
    """Requested rights. A manifest containing these values grants nothing."""

    project: ProjectAuthority | None = None
    network: NetworkAuthority | None = None
    child: ChildAuthority | None = None
    provider_ids: frozenset[str] = frozenset()
    tool_ids: frozenset[str] = frozenset()
    credential_disclosures: tuple[CredentialDisclosure, ...] = ()

    def __post_init__(self) -> None:
        for value, expected, field in (
            (self.project, ProjectAuthority, "project"),
            (self.network, NetworkAuthority, "network"),
            (self.child, ChildAuthority, "child"),
        ):
            if value is not None and not isinstance(value, expected):
                raise TypeError(f"{field} requires {expected.__name__}")
        object.__setattr__(self, "provider_ids", frozenset(_strings(self.provider_ids, "provider IDs")))
        object.__setattr__(self, "tool_ids", frozenset(_strings(self.tool_ids, "tool IDs")))
        disclosures = tuple(self.credential_disclosures)
        if any(not isinstance(item, CredentialDisclosure) for item in disclosures):
            raise TypeError("credential disclosures require CredentialDisclosure values")
        if len(disclosures) != len(set(disclosures)):
            raise ValueError("credential disclosures must not contain duplicates")
        object.__setattr__(self, "credential_disclosures", disclosures)

    @classmethod
    def from_dict(cls, value: object) -> AuthorityDeclaration:
        raw = _object(value, frozenset({"project", "network", "child", "provider_ids", "tool_ids", "credential_disclosures"}), "module authority")
        disclosures = raw.get("credential_disclosures", ())
        if not isinstance(disclosures, (list, tuple)):
            raise ValueError("credential_disclosures must be an array")
        return cls(
            project=None if raw.get("project") is None else ProjectAuthority.from_dict(raw["project"]),
            network=None if raw.get("network") is None else NetworkAuthority.from_dict(raw["network"]),
            child=None if raw.get("child") is None else ChildAuthority.from_dict(raw["child"]),
            provider_ids=frozenset(_strings(raw.get("provider_ids", ()), "provider IDs")),
            tool_ids=frozenset(_strings(raw.get("tool_ids", ()), "tool IDs")),
            credential_disclosures=tuple(CredentialDisclosure.from_dict(item) for item in disclosures),
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "project": None if self.project is None else self.project.to_dict(),
            "network": None if self.network is None else self.network.to_dict(),
            "child": None if self.child is None else self.child.to_dict(),
            "provider_ids": sorted(self.provider_ids),
            "tool_ids": sorted(self.tool_ids),
            "credential_disclosures": [item.to_dict() for item in self.credential_disclosures],
        }


@dataclass(frozen=True, slots=True)
class AdmissionGrant:
    """Admission-owner identity and rights, never constructed from a manifest."""

    grant_id: str
    authority_epoch: int
    declaration: AuthorityDeclaration
    expires_at_ms: int | None = None

    def __post_init__(self) -> None:
        _text(self.grant_id, "grant ID")
        if type(self.authority_epoch) is not int or self.authority_epoch < 1:
            raise ValueError("authority_epoch must be a positive integer")
        if not isinstance(self.declaration, AuthorityDeclaration):
            raise TypeError("grant declaration requires AuthorityDeclaration")
        if self.expires_at_ms is not None and (type(self.expires_at_ms) is not int or self.expires_at_ms < 0):
            raise ValueError("expires_at_ms must be a non-negative integer or null")

    def to_dict(self) -> dict[str, object]:
        return {
            "grant_id": self.grant_id,
            "authority_epoch": self.authority_epoch,
            "declaration": self.declaration.to_dict(),
            "expires_at_ms": self.expires_at_ms,
        }

    @classmethod
    def from_dict(cls, value: object) -> AdmissionGrant:
        raw = _object(value, frozenset({"grant_id", "authority_epoch", "declaration", "expires_at_ms"}), "admission grant")
        return cls(
            _text(raw.get("grant_id"), "grant ID"),
            raw.get("authority_epoch"),
            AuthorityDeclaration.from_dict(raw.get("declaration")),
            raw.get("expires_at_ms"),
        )
