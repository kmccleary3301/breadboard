#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path


def parse(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return rows


def render(rows: list[dict]) -> str:
    out = []
    for i, row in enumerate(rows, start=1):
        t = row.get("type", "-")
        part = row.get("part", {}) or {}
        if t == "tool_use":
            tool = part.get("tool", "-")
            state = part.get("state", {}) or {}
            status = state.get("status", "-")
            desc = (state.get("input", {}) or {}).get("description", "")
            if not desc:
                desc = (state.get("input", {}) or {}).get("prompt", "")
            desc = (desc or "").strip().replace("\n", " ")
            if len(desc) > 140:
                desc = desc[:137] + "..."
            out.append(f"{i:03d} tool_use {tool} {status} :: {desc}")
        elif t in {"step_start", "step_finish", "text"}:
            reason = part.get("reason", "")
            out.append(f"{i:03d} {t} {reason}".rstrip())
        else:
            out.append(f"{i:03d} {t}")
    return "\n".join(out) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("input_jsonl")
    ap.add_argument("output_txt")
    args = ap.parse_args()

    inp = Path(args.input_jsonl)
    out = Path(args.output_txt)
    rows = parse(inp)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(rows), encoding="utf-8")
    print(f"[opencode-digest] wrote {out} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
