#!/usr/bin/env python3
"""Validate replay JSONL records for non-ambiguous artifact_ref usage."""

from __future__ import annotations

import argparse
import glob
import json
from pathlib import Path
from typing import Any


TOOL_RESULT_TYPES = {"tool_result", "tool.result"}


def _is_record(value: Any) -> bool:
    return isinstance(value, dict)


def _has_inline_payload(display: dict[str, Any], payload: dict[str, Any]) -> bool:
    detail = display.get("detail")
    diff_blocks = display.get("diff_blocks")
    result = payload.get("result")
    content = payload.get("content")
    return bool(detail) or bool(diff_blocks) or isinstance(result, str) and len(result) > 0 or isinstance(content, str) and len(content) > 0


def _extract_artifact_ref(display: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any] | None:
    direct = payload.get("artifact_ref")
    if _is_record(direct):
        return direct  # type: ignore[return-value]
    nested = display.get("detail_artifact")
    if _is_record(nested):
        return nested  # type: ignore[return-value]
    return None


def _validate_ref_shape(ref: dict[str, Any]) -> str | None:
    required = ["schema_version", "id", "kind", "mime", "size_bytes", "sha256", "storage", "path"]
    for key in required:
        if key not in ref:
            return f"missing artifact_ref.{key}"
    if ref.get("schema_version") != "artifact_ref_v1":
        return "artifact_ref.schema_version must be artifact_ref_v1"
    if ref.get("storage") != "workspace_file":
        return "artifact_ref.storage must be workspace_file"
    if not isinstance(ref.get("path"), str) or not str(ref.get("path")).strip():
        return "artifact_ref.path must be a non-empty string"
    return None


def validate_jsonl(path: Path) -> list[str]:
    errors: list[str] = []
    with path.open("r", encoding="utf-8") as handle:
        for line_no, raw in enumerate(handle, start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            event_type = str(payload.get("type", ""))
            if event_type not in TOOL_RESULT_TYPES:
                continue
            event_payload = payload.get("payload")
            if not isinstance(event_payload, dict):
                continue
            display = event_payload.get("display")
            display_record = display if isinstance(display, dict) else {}
            artifact_ref = _extract_artifact_ref(display_record, event_payload)
            has_inline = _has_inline_payload(display_record, event_payload)
            if artifact_ref is not None and has_inline:
                errors.append(f"{path}:{line_no}: ambiguous payload has inline data and artifact_ref")
                continue
            if artifact_ref is not None:
                shape_error = _validate_ref_shape(artifact_ref)
                if shape_error:
                    errors.append(f"{path}:{line_no}: {shape_error}")
    return errors


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate replay artifact_ref contract in JSONL fixtures.")
    parser.add_argument(
        "--glob",
        dest="glob_pattern",
        default="config/cli_bridge_replays/**/*.jsonl",
        help="glob for replay JSONL fixture files",
    )
    parser.add_argument("--output-json", default="", help="optional output JSON report path")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    files = sorted(Path(p) for p in glob.glob(args.glob_pattern, recursive=True))
    all_errors: list[str] = []
    for file_path in files:
        all_errors.extend(validate_jsonl(file_path))
    report = {
        "schema_version": "replay_artifact_ref_check_v1",
        "glob": args.glob_pattern,
        "files_checked": len(files),
        "error_count": len(all_errors),
        "errors": all_errors,
        "ok": len(all_errors) == 0,
    }
    if args.output_json:
        output_path = Path(args.output_json)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    if all_errors:
        print("[replay-artifact-ref] fail")
        for line in all_errors:
            print(f"- {line}")
        return 2
    print(f"[replay-artifact-ref] pass ({len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
