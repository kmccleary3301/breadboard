#!/usr/bin/env python3
"""
Generate a lightweight HTML gallery for a tmux capture run directory.

The output is a single self-contained HTML file referencing frame PNG/TXT/ANSI
artifacts from the run directory.
"""

from __future__ import annotations

import argparse
import html
import json
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _sample_indices(total: int, limit: int) -> list[int]:
    if limit <= 0 or total <= limit:
        return list(range(total))
    if limit == 1:
        return [total - 1]
    last = total - 1
    picks: list[int] = []
    for i in range(limit):
        idx = round((i * last) / (limit - 1))
        if idx not in picks:
            picks.append(idx)
    while len(picks) < limit:
        for idx in range(total):
            if idx not in picks:
                picks.append(idx)
                if len(picks) >= limit:
                    break
    return sorted(picks)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Generate HTML gallery for a tmux capture run.")
    p.add_argument("--run-dir", required=True, help="run directory containing frames/ and manifest files")
    p.add_argument("--output-html", default="", help="output HTML path (default: <run-dir>/gallery.html)")
    p.add_argument("--title", default="", help="gallery title override")
    p.add_argument("--max-frames", type=int, default=0, help="max frames to render (0 = all)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    run_dir = Path(args.run_dir).expanduser().resolve()
    if not run_dir.exists() or not run_dir.is_dir():
        raise FileNotFoundError(f"run dir not found: {run_dir}")

    output_html = (
        Path(args.output_html).expanduser().resolve()
        if args.output_html
        else (run_dir / "gallery.html")
    )

    summary = _load_json(run_dir / "run_summary.json")
    manifest = _load_json(run_dir / "scenario_manifest.json")
    scenario = str(summary.get("scenario") or manifest.get("scenario") or "unknown")
    capture_mode = str(summary.get("capture_mode") or manifest.get("capture_mode") or "unknown")
    frame_dir = run_dir / "frames"
    png_frames = sorted(frame_dir.glob("frame_*.png"))
    if not png_frames:
        raise FileNotFoundError(f"no frame PNGs found in {frame_dir}")

    pick_indices = _sample_indices(len(png_frames), int(args.max_frames))
    selected = [png_frames[i] for i in pick_indices]

    title = args.title.strip() or f"tmux gallery: {scenario}"
    cards: list[str] = []
    for png in selected:
        stem = png.stem
        txt = frame_dir / f"{stem}.txt"
        ansi = frame_dir / f"{stem}.ansi"
        rel_png = png.relative_to(run_dir).as_posix()
        rel_txt = txt.relative_to(run_dir).as_posix() if txt.exists() else ""
        rel_ansi = ansi.relative_to(run_dir).as_posix() if ansi.exists() else ""
        links = [f'<a href="{html.escape(rel_png)}">png</a>']
        if rel_txt:
            links.append(f'<a href="{html.escape(rel_txt)}">txt</a>')
        if rel_ansi:
            links.append(f'<a href="{html.escape(rel_ansi)}">ansi</a>')
        cards.append(
            f"""
<article class="card">
  <header>
    <h3>{html.escape(stem)}</h3>
    <p>{" | ".join(links)}</p>
  </header>
  <img src="{html.escape(rel_png)}" alt="{html.escape(stem)}" loading="lazy" />
</article>""".strip()
        )

    doc = f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>{html.escape(title)}</title>
  <style>
    :root {{
      --bg: #0f172a;
      --panel: #111827;
      --fg: #e5e7eb;
      --muted: #94a3b8;
      --border: #334155;
      --link: #7dd3fc;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
      background: var(--bg);
      color: var(--fg);
    }}
    main {{
      max-width: 1400px;
      margin: 0 auto;
      padding: 20px;
    }}
    .meta {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      padding: 12px 14px;
      margin-bottom: 16px;
      line-height: 1.5;
    }}
    .meta code {{ color: var(--muted); }}
    a {{ color: var(--link); text-decoration: none; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(380px, 1fr));
      gap: 14px;
    }}
    .card {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 8px;
      overflow: hidden;
    }}
    .card header {{
      padding: 10px 12px;
      border-bottom: 1px solid var(--border);
    }}
    .card h3 {{
      margin: 0;
      font-size: 13px;
      font-weight: 700;
      letter-spacing: 0.01em;
    }}
    .card p {{
      margin: 4px 0 0 0;
      font-size: 12px;
      color: var(--muted);
    }}
    .card img {{
      display: block;
      width: 100%;
      height: auto;
      background: #020617;
    }}
  </style>
</head>
<body>
  <main>
    <h1>{html.escape(title)}</h1>
    <section class="meta">
      <div>scenario: <code>{html.escape(scenario)}</code></div>
      <div>capture_mode: <code>{html.escape(capture_mode)}</code></div>
      <div>run_dir: <code>{html.escape(str(run_dir))}</code></div>
      <div>frames shown: <code>{len(selected)}</code> / total <code>{len(png_frames)}</code></div>
    </section>
    <section class="grid">
      {"".join(cards)}
    </section>
  </main>
</body>
</html>
"""
    output_html.write_text(doc, encoding="utf-8")
    print(str(output_html))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
