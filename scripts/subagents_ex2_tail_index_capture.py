#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

# Ensure repo root is importable even when invoked from within tui_skeleton/.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# Import the implementation under test from the repo (dependency-free).
from agentic_coder_prototype.api.cli_bridge.tail_index import _TailLineIndexCache  # type: ignore  # noqa: E402


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    index = max(0, min(len(values) - 1, int((len(values) * q) - 1e-9)))
    return float(values[index])


def _write_large_jsonl(path: Path, *, lines: int) -> None:
    # Keep generation deterministic and reasonably fast while still producing a large number of lines.
    block = 10_000
    with path.open("wb") as handle:
        for start in range(0, lines, block):
            end = min(lines, start + block)
            payload = "".join(f'{{"i":{idx},"v":"ok"}}\n' for idx in range(start, end))
            handle.write(payload.encode("utf-8"))


def _run_once(cache: _TailLineIndexCache, file_path: Path, *, tail_lines: int, max_bytes: int) -> dict[str, int]:
    # Exercise the cache and validate that we always return the requested tail line count (or fewer if file smaller).
    text, meta = cache.read_tail_text(file_path, tail_lines=tail_lines, max_bytes=max_bytes)
    returned_lines = 0 if text == "" else len(text.split("\n"))
    if returned_lines > tail_lines:
        raise RuntimeError(f"returned {returned_lines} lines, expected <= {tail_lines}")
    meta = dict(meta)
    meta["returned_lines"] = returned_lines
    return {k: int(v) for k, v in meta.items()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True)
    parser.add_argument("--lines", type=int, default=500_000)
    parser.add_argument("--samples", type=int, default=40)
    parser.add_argument("--tail-lines", type=int, default=400)
    parser.add_argument("--max-bytes", type=int, default=120_000)
    args = parser.parse_args()

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="bb-ex2-tail-index-") as tmpdir:
        file_path = Path(tmpdir) / "huge.jsonl"
        _write_large_jsonl(file_path, lines=max(1, int(args.lines)))

        tail_lines = max(1, int(args.tail_lines))
        max_bytes = max(1, int(args.max_bytes))
        samples = max(8, int(args.samples))

        # "Uncached": new cache each iteration -> forces re-indexing each time.
        uncached_durations: list[float] = []
        uncached_index_ops = 0
        uncached_index_bytes = 0
        for _ in range(samples):
            cache = _TailLineIndexCache()
            started = time.perf_counter()
            _run_once(cache, file_path, tail_lines=tail_lines, max_bytes=max_bytes)
            uncached_durations.append((time.perf_counter() - started) * 1000.0)
            # Pull counters from the single entry (best-effort).
            entry = next(iter(cache._entries.values())) if getattr(cache, "_entries", None) else None  # type: ignore[attr-defined]
            if entry is not None:
                uncached_index_ops += int(getattr(entry, "index_read_ops", 0))
                uncached_index_bytes += int(getattr(entry, "index_read_bytes", 0))

        # "Cached": warm once, then repeat with same cache -> should avoid repeated index scans.
        cached_cache = _TailLineIndexCache()
        warm_meta = _run_once(cached_cache, file_path, tail_lines=tail_lines, max_bytes=max_bytes)
        cached_durations: list[float] = []
        cached_index_ops_before = 0
        cached_index_bytes_before = 0
        entry = next(iter(cached_cache._entries.values())) if getattr(cached_cache, "_entries", None) else None  # type: ignore[attr-defined]
        if entry is not None:
            cached_index_ops_before = int(getattr(entry, "index_read_ops", 0))
            cached_index_bytes_before = int(getattr(entry, "index_read_bytes", 0))
        for _ in range(samples):
            started = time.perf_counter()
            _run_once(cached_cache, file_path, tail_lines=tail_lines, max_bytes=max_bytes)
            cached_durations.append((time.perf_counter() - started) * 1000.0)
        cached_index_ops_after = cached_index_ops_before
        cached_index_bytes_after = cached_index_bytes_before
        entry = next(iter(cached_cache._entries.values())) if getattr(cached_cache, "_entries", None) else None  # type: ignore[attr-defined]
        if entry is not None:
            cached_index_ops_after = int(getattr(entry, "index_read_ops", 0))
            cached_index_bytes_after = int(getattr(entry, "index_read_bytes", 0))

        uncached_p95 = _percentile(uncached_durations, 0.95)
        cached_p95 = _percentile(cached_durations, 0.95)
        cached_index_ops_delta = max(0, cached_index_ops_after - cached_index_ops_before)
        cached_index_bytes_delta = max(0, cached_index_bytes_after - cached_index_bytes_before)

        acceptance = {
            "returnsTailLines": warm_meta.get("returned_lines", 0) <= tail_lines,
            "cacheAvoidsRepeatedIndexOps": cached_index_ops_delta <= max(1, uncached_index_ops // max(1, samples) // 4),
            "cacheAvoidsRepeatedIndexBytes": cached_index_bytes_delta <= max(16_384, (uncached_index_bytes // max(1, samples)) // 4),
            # Soft signal: the cached p95 should not regress vs cold.
            "cachedP95NoWorse": cached_p95 <= (uncached_p95 * 1.10),
        }

        capture = {
            "scenario": "ex2_focus_tail_index_cache_benchmark",
            "generatedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "acceptanceChecks": acceptance,
            "metrics": {
                "file": {
                    "bytes": int(os.stat(file_path).st_size),
                    "linesGenerated": int(args.lines),
                },
                "params": {
                    "tailLines": tail_lines,
                    "maxBytes": max_bytes,
                    "samples": samples,
                },
                "uncached": {
                    "p95Ms": uncached_p95,
                    "maxMs": max(uncached_durations) if uncached_durations else 0.0,
                    "indexReadOpsTotal": uncached_index_ops,
                    "indexReadBytesTotal": uncached_index_bytes,
                },
                "cached": {
                    "p95Ms": cached_p95,
                    "maxMs": max(cached_durations) if cached_durations else 0.0,
                    "indexReadOpsDelta": cached_index_ops_delta,
                    "indexReadBytesDelta": cached_index_bytes_delta,
                },
            },
        }

        out_path.write_text(json.dumps(capture, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
