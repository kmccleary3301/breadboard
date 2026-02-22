#!/usr/bin/env python3
"""
Validate Phase4 footer/input-bar contract in final tmux capture frame.

Contract (text-capture):
- A horizontal border line exists directly above the prompt row.
- Prompt row contains the expected prompt anchor text and starts with prompt glyph (`❯` or `>`).
- A horizontal border line exists directly below the prompt row.
- Footer cues include shortcuts + status anchors near the prompt footer area.
  - Legacy layout: both anchors share one shortcuts row.
  - FooterV2 layout: status can live on the phase row while shortcuts are on summary row.
- Overlay replacement mode is accepted when shortcuts/status cues are immediately above
  a modal top border.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_PROMPT_ANCHOR = 'Try "fix typecheck errors"'
DEFAULT_SHORTCUTS_ANCHOR = "? for shortcuts"
DEFAULT_STATUS_ANCHOR = "Cooked for"


def _anchor_variants(anchor: str) -> list[str]:
    token = str(anchor or "").strip()
    if token in {"? for shortcuts", "? shortcuts"}:
        return ["? for shortcuts", "? shortcuts"]
    if token in {"Cooked for", "last"}:
        return ["Cooked for", "last "]
    return [token] if token else []


def _contains_anchor(text: str, anchor: str) -> bool:
    return any(variant in text for variant in _anchor_variants(anchor))


@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    run_dir: str
    frame_count: int
    final_text_path: str
    prompt_line_index: int
    shortcuts_line_index: int
    contract_mode: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": self.errors,
            "run_dir": self.run_dir,
            "frame_count": self.frame_count,
            "final_text_path": self.final_text_path,
            "prompt_line_index": self.prompt_line_index,
            "shortcuts_line_index": self.shortcuts_line_index,
            "contract_mode": self.contract_mode,
        }


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected object JSON at {path}")
    return payload


def _discover_run_dir(path: Path) -> Path:
    root = path.expanduser().resolve()
    if not root.exists():
        raise FileNotFoundError(f"run-dir path not found: {root}")
    if root.is_file():
        root = root.parent
    if (root / "index.jsonl").exists():
        return root
    manifests = sorted(root.rglob("index.jsonl"))
    if manifests:
        return manifests[-1].parent
    raise FileNotFoundError(f"index.jsonl not found under {root}")


def _last_frame_text_path(run_dir: Path) -> tuple[Path, int]:
    index_path = run_dir / "index.jsonl"
    if not index_path.exists():
        raise FileNotFoundError(f"index.jsonl missing: {index_path}")

    last_record: dict[str, Any] | None = None
    frame_count = 0
    with index_path.open("r", encoding="utf-8") as f:
        for raw in f:
            line = raw.strip()
            if not line:
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                continue
            frame_count += 1
            last_record = payload

    if frame_count == 0 or last_record is None:
        raise ValueError(f"index.jsonl has no frame records: {index_path}")

    rel = str(last_record.get("text") or "").strip()
    if not rel:
        raise ValueError("last frame record missing text path")
    text_path = (run_dir / rel).resolve()
    if not text_path.exists():
        raise FileNotFoundError(f"final text frame missing: {text_path}")
    return text_path, frame_count


def _is_border_line(line: str) -> bool:
    trimmed = line.strip()
    if not trimmed:
        return False
    horizontal = sum(1 for ch in trimmed if ch in {"─", "-", "━", "═"})
    return horizontal >= 8


def _find_last_line_index(lines: list[str], anchor: str) -> int:
    for idx in range(len(lines) - 1, -1, -1):
        if _contains_anchor(lines[idx], anchor):
            return idx
    return -1


def _find_last_shortcuts_status_line(
    lines: list[str],
    shortcuts_anchor: str,
    status_anchor: str,
) -> int:
    for idx in range(len(lines) - 1, -1, -1):
        line = lines[idx]
        if _contains_anchor(line, shortcuts_anchor) and _contains_anchor(line, status_anchor):
            return idx
    return -1


def _is_modal_top_border(line: str) -> bool:
    trimmed = line.strip()
    if not trimmed:
        return False
    return (trimmed.startswith("╭") and trimmed.endswith("╮")) or (
        trimmed.startswith("+") and trimmed.endswith("+")
    )


def _find_overlay_shortcuts_line(
    lines: list[str],
    *,
    shortcuts_anchor: str,
    status_anchor: str,
) -> int:
    for idx in range(len(lines) - 1, -1, -1):
        line = lines[idx]
        if not _contains_anchor(line, shortcuts_anchor):
            continue
        near_start = max(0, idx - 2)
        near_end = min(len(lines), idx + 2)
        if not any(_contains_anchor(lines[pos], status_anchor) for pos in range(near_start, near_end)):
            continue
        candidate_idx = idx + 1
        while candidate_idx < len(lines) and not lines[candidate_idx].strip():
            candidate_idx += 1
        if candidate_idx < len(lines) and _is_modal_top_border(lines[candidate_idx]):
            return idx
    return -1


def validate_footer_contract(
    run_dir: Path,
    *,
    prompt_anchor: str = DEFAULT_PROMPT_ANCHOR,
    shortcuts_anchor: str = DEFAULT_SHORTCUTS_ANCHOR,
    status_anchor: str = DEFAULT_STATUS_ANCHOR,
) -> ValidationResult:
    errors: list[str] = []
    final_text_path, frame_count = _last_frame_text_path(run_dir)
    lines = final_text_path.read_text(encoding="utf-8", errors="replace").splitlines()
    scenario = ""
    manifest_path = run_dir / "scenario_manifest.json"
    if manifest_path.exists():
        try:
            manifest = _load_json(manifest_path)
            scenario = str(manifest.get("scenario") or "")
        except Exception:
            scenario = ""
    is_stress_tasklist_scenario = scenario == "phase4_replay/subagents_concurrency_20_v1"

    prompt_idx = _find_last_line_index(lines, prompt_anchor)
    shortcuts_idx = -1
    contract_mode = "unknown"

    overlay_shortcuts_idx = _find_overlay_shortcuts_line(
        lines,
        shortcuts_anchor=shortcuts_anchor,
        status_anchor=status_anchor,
    )
    if overlay_shortcuts_idx >= 0:
        contract_mode = "overlay_replacement"
        shortcuts_idx = overlay_shortcuts_idx

    if contract_mode != "overlay_replacement":
        contract_mode = "classic_input"
        if prompt_idx < 0:
            running_rows = sum(1 for line in lines if " running · " in line)
            has_shortcuts = any(_contains_anchor(line, shortcuts_anchor) for line in lines)
            if is_stress_tasklist_scenario and has_shortcuts and running_rows >= 8:
                contract_mode = "tasklist_fullpane_stress"
                shortcuts_idx = _find_last_line_index(lines, shortcuts_anchor)
                return ValidationResult(
                    ok=True,
                    errors=[],
                    run_dir=str(run_dir),
                    frame_count=frame_count,
                    final_text_path=str(final_text_path),
                    prompt_line_index=prompt_idx,
                    shortcuts_line_index=shortcuts_idx,
                    contract_mode=contract_mode,
                )
            errors.append(f"prompt anchor not found: {prompt_anchor!r}")
        else:
            prompt_line = lines[prompt_idx]
            stripped = prompt_line.lstrip()
            if not (stripped.startswith("❯ ") or stripped.startswith("> ")):
                errors.append(
                    "prompt row does not start with prompt glyph (`❯` or `>`): "
                    f"{prompt_line!r}"
                )

            if prompt_idx == 0:
                errors.append("missing border line above prompt row")
            elif not _is_border_line(lines[prompt_idx - 1]):
                errors.append(
                    "line above prompt row is not a border line: "
                    f"{lines[prompt_idx - 1]!r}"
                )

            lower_border_idx = prompt_idx + 1
            if lower_border_idx >= len(lines):
                errors.append("missing border line below prompt row")
            elif not _is_border_line(lines[lower_border_idx]):
                errors.append(
                    "line below prompt row is not a border line: "
                    f"{lines[lower_border_idx]!r}"
                )

            footer_start = prompt_idx + 2
            footer_end = min(len(lines), prompt_idx + 7)
            footer_window = lines[footer_start:footer_end] if footer_start < len(lines) else []
            if not footer_window:
                errors.append("missing footer rows below input border")
            else:
                found_shortcuts = False
                found_status = False
                for rel_idx, line in enumerate(footer_window):
                    if _contains_anchor(line, shortcuts_anchor):
                        found_shortcuts = True
                        shortcuts_idx = footer_start + rel_idx
                    if _contains_anchor(line, status_anchor):
                        found_status = True
                if not found_shortcuts:
                    errors.append(
                        f"shortcuts anchor missing from footer rows: {shortcuts_anchor!r}"
                    )
                if not found_status:
                    errors.append(
                        f"status anchor missing from footer rows: {status_anchor!r}"
                    )

    return ValidationResult(
        ok=(len(errors) == 0),
        errors=errors,
        run_dir=str(run_dir),
        frame_count=frame_count,
        final_text_path=str(final_text_path),
        prompt_line_index=prompt_idx,
        shortcuts_line_index=shortcuts_idx,
        contract_mode=contract_mode,
    )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Validate Phase4 footer contract from final capture frame.")
    p.add_argument("--run-dir", required=True, help="run dir or parent containing capture runs")
    p.add_argument("--prompt-anchor", default=DEFAULT_PROMPT_ANCHOR, help="expected prompt row anchor text")
    p.add_argument("--shortcuts-anchor", default=DEFAULT_SHORTCUTS_ANCHOR, help="expected shortcuts row anchor text")
    p.add_argument("--status-anchor", default=DEFAULT_STATUS_ANCHOR, help="expected right-status anchor text")
    p.add_argument("--output-json", default="", help="optional output report path")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    try:
        run_dir = _discover_run_dir(Path(args.run_dir))
        result = validate_footer_contract(
            run_dir,
            prompt_anchor=str(args.prompt_anchor),
            shortcuts_anchor=str(args.shortcuts_anchor),
            status_anchor=str(args.status_anchor),
        )
        out_json = (
            Path(args.output_json).expanduser().resolve()
            if args.output_json
            else run_dir / "footer_contract_report.json"
        )
        out_json.write_text(json.dumps(result.to_dict(), indent=2) + "\n", encoding="utf-8")
        if result.ok:
            print(f"[phase4-footer-contract] pass: {run_dir}")
            return 0
        print(f"[phase4-footer-contract] fail: {run_dir}")
        for err in result.errors:
            print(f"- {err}")
        return 2
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[phase4-footer-contract] error: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
