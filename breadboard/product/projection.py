"""Small immutable vocabulary shared by domain-owned projections."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Generic, TypeVar


T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class ProjectionCursor:
    """One stream's source position in a multi-stream projection."""

    stream: str
    sequence: int

    def __post_init__(self) -> None:
        if type(self.stream) is not str or not self.stream:
            raise ValueError("projection cursor stream must be a non-empty string")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("projection cursor sequence must be positive")


ProjectionAsOf = int | tuple[ProjectionCursor, ...]


@dataclass(frozen=True, slots=True)
class ProjectionSource:
    """The source sequence range that produced a projected value."""

    stream: str
    first_sequence: int
    last_sequence: int
    components: tuple["ProjectionSource", ...] = ()

    def __post_init__(self) -> None:
        if type(self.stream) is not str or not self.stream:
            raise ValueError("projection source stream must be a non-empty string")
        if type(self.first_sequence) is not int or self.first_sequence < 1:
            raise ValueError("projection source first_sequence must be positive")
        if type(self.last_sequence) is not int or self.last_sequence < self.first_sequence:
            raise ValueError("projection source last_sequence must not precede first_sequence")
        if any(not isinstance(component, ProjectionSource) for component in self.components):
            raise TypeError("projection source components must be ProjectionSource values")


@dataclass(frozen=True, slots=True)
class Projected(Generic[T]):
    """A domain value plus the evidence needed to audit its fold."""

    value: T
    projector_version: str
    source: ProjectionSource
    as_of: ProjectionAsOf

    def __post_init__(self) -> None:
        if type(self.projector_version) is not str or not self.projector_version:
            raise ValueError("projector_version must be a non-empty string")
        if type(self.as_of) is int:
            if self.as_of < 1:
                raise ValueError("projection as_of must be positive")
        elif type(self.as_of) is tuple:
            if not self.as_of or any(not isinstance(cursor, ProjectionCursor) for cursor in self.as_of):
                raise ValueError("projection as_of cursor vector must be non-empty and typed")
        else:
            raise TypeError("projection as_of must be an integer or cursor vector")
