from __future__ import annotations

import json
import warnings
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

from breadboard.product.evidence.e4.lane_definitions import LaneDefValidationError
from breadboard.product.evidence.e4.paths import SOURCE_ROOT

LANE_MANIFEST_SCHEMA_PATH = (
    SOURCE_ROOT / "contracts/kernel/schemas/bb.e4.lane_manifest.v1.schema.json"
)
MANIFEST_REVIEW_LINE_LIMIT = 150
MANIFEST_HARD_LINE_LIMIT = 300
MANIFEST_MAX_LINE_LENGTH = 120


def _manifest_flow_style_errors(node: yaml.Node, pointer: str = "") -> list[str]:
    errors: list[str] = []
    if isinstance(node, yaml.MappingNode):
        if node.flow_style and len(node.value) > 1:
            errors.append(
                f"{pointer or '<root>'}: flow-style mapping has {len(node.value)} keys; "
                "lane manifests require block style"
            )
        for key_node, value_node in node.value:
            key = str(key_node.value)
            child_pointer = f"{pointer}/{key}" if pointer else f"/{key}"
            errors.extend(_manifest_flow_style_errors(value_node, child_pointer))
    elif isinstance(node, yaml.SequenceNode):
        for index, item_node in enumerate(node.value):
            child_pointer = f"{pointer}/{index}" if pointer else f"/{index}"
            errors.extend(_manifest_flow_style_errors(item_node, child_pointer))
    return errors


def load_lane_manifest(path: Path) -> dict[str, Any]:
    """Load and validate an author-owned manifest before lock resolution."""

    text = path.read_text(encoding="utf-8")
    if "sha256:" in text:
        raise LaneDefValidationError(
            f"{path}: manifest contains forbidden 'sha256:' substring; "
            "digests belong in the lane lock"
        )

    overlong = [
        number
        for number, line in enumerate(text.splitlines(), start=1)
        if len(line) > MANIFEST_MAX_LINE_LENGTH
    ]
    if overlong:
        raise LaneDefValidationError(
            f"{path}: manifest lines exceed {MANIFEST_MAX_LINE_LENGTH} characters: "
            + ", ".join(str(number) for number in overlong)
        )

    canonical_lines = sum(
        1
        for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )
    if canonical_lines > MANIFEST_HARD_LINE_LIMIT:
        raise LaneDefValidationError(
            f"{path}: manifest has {canonical_lines} canonical lines; "
            f"maximum is {MANIFEST_HARD_LINE_LIMIT}"
        )
    if canonical_lines > MANIFEST_REVIEW_LINE_LIMIT:
        warnings.warn(
            f"{path}: manifest has {canonical_lines} canonical lines; "
            f"review budget is {MANIFEST_REVIEW_LINE_LIMIT}",
            UserWarning,
            stacklevel=2,
        )

    try:
        node = yaml.compose(text)
        payload = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise LaneDefValidationError(f"{path}: invalid YAML: {exc}") from exc
    if node is None or not isinstance(payload, dict):
        raise LaneDefValidationError(f"{path}: manifest must contain a YAML mapping")
    flow_errors = _manifest_flow_style_errors(node)
    if flow_errors:
        raise LaneDefValidationError(f"{path}: " + "; ".join(flow_errors))

    schema = json.loads(LANE_MANIFEST_SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = sorted(
        (
            (
                "/" + "/".join(str(part) for part in error.absolute_path)
                if error.absolute_path
                else "<root>"
            )
            + f": {error.message}"
            for error in Draft202012Validator(schema).iter_errors(payload)
        )
    )
    if errors:
        raise LaneDefValidationError(f"{path}: " + "; ".join(errors))
    return payload
