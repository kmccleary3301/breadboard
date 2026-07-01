from __future__ import annotations

from pathlib import Path
from typing import Any, Literal, Mapping, NotRequired, Protocol, TypedDict


class ComparatorAssertion(TypedDict):
    assertion_id: str
    status: Literal["passed", "failed", "warned"]
    observed: Any
    expected: Any
    detail: str


class ComparatorInput(TypedDict):
    capture: dict[str, Any]
    replay: dict[str, Any]
    scope: dict[str, Any]
    artifacts: Mapping[str, Path]
    repo_root: NotRequired[Path]


class Comparator(Protocol):
    def __call__(self, inp: ComparatorInput) -> dict[str, Any]:
        """Compare capture/replay artifacts and return a bb.e4.comparator_report.v1-shaped record."""
