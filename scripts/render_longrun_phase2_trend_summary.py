#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, List, Mapping


def _load_history(path: Path) -> List[Mapping[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("history file must be a JSON array")
    out: List[Mapping[str, Any]] = []
    for row in payload:
        if isinstance(row, Mapping):
            out.append(row)
    return out


def render_markdown(history: List[Mapping[str, Any]]) -> str:
    lines: List[str] = []
    lines.append("# LongRun Phase2 Strict Gate Trend")
    lines.append("")
    lines.append(f"- Total recorded runs: `{len(history)}`")
    pass_count = sum(1 for row in history if bool(row.get("report_ok")))
    lines.append(f"- Pass count: `{pass_count}`")
    lines.append(f"- Pass rate: `{(pass_count / len(history) * 100.0):.1f}%`" if history else "- Pass rate: `n/a`")
    lines.append("")
    lines.append("| Time (UTC) | Report OK | Parity OK | Boundedness | Stable Success | No Regression | Ref | SHA |")
    lines.append("|---|---:|---:|---:|---:|---:|---|---|")
    for row in history[-30:]:
        ts = int(row.get("ts_unix") or 0)
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S") if ts > 0 else "n/a"
        lines.append(
            "| {dt} | {ok} | {par} | {bnd} | {stb} | {noreg} | {ref} | {sha} |".format(
                dt=dt,
                ok="✅" if bool(row.get("report_ok")) else "❌",
                par="✅" if bool(row.get("parity_audit_ok")) else "❌",
                bnd="✅" if bool(row.get("boundedness_required_ok")) else "❌",
                stb="✅" if bool(row.get("stable_success_path_ok")) else "❌",
                noreg="✅" if bool(row.get("no_success_regression_ok")) else "❌",
                ref=str(row.get("github_ref") or "local"),
                sha=str(row.get("github_sha") or "")[:12],
            )
        )
    lines.append("")
    lines.append("## Notes")
    lines.append("- Trend is generated from strict-gate go/no-go reports.")
    lines.append("- Use this to detect drift before promoting policy changes.")
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Render longrun phase2 strict-gate trend markdown from history.")
    parser.add_argument("--history-json", required=True, help="Path to history JSON.")
    parser.add_argument("--markdown-out", required=True, help="Output markdown file path.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    history = _load_history(Path(args.history_json))
    markdown = render_markdown(history)
    out = Path(args.markdown_out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(markdown, encoding="utf-8")
    print(f"Wrote markdown: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
