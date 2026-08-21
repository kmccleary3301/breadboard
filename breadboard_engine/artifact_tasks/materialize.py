from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping

from .contracts import hash_file, safe_relative_path


_FENCE_RE = re.compile(r"```(?P<language>[A-Za-z0-9_+.#-]*)[ \t]*\n(?P<body>.*?)\n```", re.DOTALL)


@dataclass(frozen=True)
class MaterializationSpec:
    strategy: str = "fenced_block"
    language: str | None = None
    output_path: str = ""
    require_single_block: bool = True
    strip_surrounding_whitespace: bool = True
    reject_empty: bool = True
    overwrite: bool = False

    def __post_init__(self) -> None:
        if self.strategy != "fenced_block":
            raise ValueError("only fenced_block materialization is supported")
        object.__setattr__(self, "language", str(self.language).strip().lower() if self.language else None)
        object.__setattr__(self, "output_path", str(safe_relative_path(self.output_path, field_name="output_path")))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "language": self.language,
            "output_path": self.output_path,
            "require_single_block": self.require_single_block,
            "strip_surrounding_whitespace": self.strip_surrounding_whitespace,
            "reject_empty": self.reject_empty,
            "overwrite": self.overwrite,
        }

    @staticmethod
    def from_dict(data: Mapping[str, Any]) -> "MaterializationSpec":
        return MaterializationSpec(
            strategy=str(data.get("strategy") or "fenced_block"),
            language=data.get("language"),
            output_path=str(data.get("output_path") or ""),
            require_single_block=bool(data.get("require_single_block", True)),
            strip_surrounding_whitespace=bool(data.get("strip_surrounding_whitespace", True)),
            reject_empty=bool(data.get("reject_empty", True)),
            overwrite=bool(data.get("overwrite", False)),
        )


@dataclass(frozen=True)
class MaterializationResult:
    ok: bool
    output_path: str
    absolute_path: str | None = None
    language: str | None = None
    byte_count: int | None = None
    sha256: str | None = None
    block_count: int = 0
    failure_reasons: tuple[str, ...] = field(default_factory=tuple)
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        payload: Dict[str, Any] = {
            "ok": self.ok,
            "output_path": self.output_path,
            "block_count": self.block_count,
            "failure_reasons": list(self.failure_reasons),
            "metadata": dict(self.metadata),
        }
        if self.absolute_path:
            payload["absolute_path"] = self.absolute_path
        if self.language:
            payload["language"] = self.language
        if self.byte_count is not None:
            payload["byte_count"] = self.byte_count
        if self.sha256:
            payload["sha256"] = self.sha256
        return payload


def _matching_blocks(response_text: str, language: str | None) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for match in _FENCE_RE.finditer(response_text):
        block_language = (match.group("language") or "").strip().lower()
        if language and block_language != language:
            continue
        blocks.append((block_language, match.group("body")))
    return blocks


def materialize_response_artifact(
    response_text: str,
    spec: MaterializationSpec,
    *,
    root: Path,
) -> MaterializationResult:
    root_path = root.resolve()
    output_rel = safe_relative_path(spec.output_path, field_name="output_path")
    output_path = (root_path / output_rel).resolve()
    try:
        output_path.relative_to(root_path)
    except ValueError:
        raise ValueError(f"output_path escapes root: {spec.output_path}") from None

    blocks = _matching_blocks(response_text, spec.language)
    if not blocks:
        return MaterializationResult(
            ok=False,
            output_path=spec.output_path,
            block_count=0,
            failure_reasons=("missing_fenced_block",),
            metadata={"required_language": spec.language},
        )
    if spec.require_single_block and len(blocks) != 1:
        return MaterializationResult(
            ok=False,
            output_path=spec.output_path,
            block_count=len(blocks),
            failure_reasons=("multiple_fenced_blocks",),
            metadata={"required_language": spec.language},
        )
    if output_path.exists() and not spec.overwrite:
        return MaterializationResult(
            ok=False,
            output_path=spec.output_path,
            absolute_path=str(output_path),
            block_count=len(blocks),
            failure_reasons=("output_exists",),
        )

    selected_language, content = blocks[0]
    if spec.strip_surrounding_whitespace:
        content = content.strip() + "\n"
    if spec.reject_empty and not content.strip():
        return MaterializationResult(
            ok=False,
            output_path=spec.output_path,
            block_count=len(blocks),
            language=selected_language or None,
            failure_reasons=("empty_fenced_block",),
        )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(content, encoding="utf-8")
    return MaterializationResult(
        ok=True,
        output_path=spec.output_path,
        absolute_path=str(output_path),
        language=selected_language or None,
        byte_count=output_path.stat().st_size,
        sha256=hash_file(output_path),
        block_count=len(blocks),
        metadata={"strategy": spec.strategy},
    )
