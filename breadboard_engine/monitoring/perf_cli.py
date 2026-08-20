#!/usr/bin/env python3
from __future__ import annotations

import argparse
from typing import List

from .performance_monitor import PerformanceMonitor


def main():
    parser = argparse.ArgumentParser(description="Show tool-calling performance summary")
    parser.add_argument("--db", default="tool_calling_performance.db", help="Path to performance DB")
    parser.add_argument("--formats", nargs="*", default=[], help="Formats to include (optional)")
    parser.add_argument("--hours", type=float, default=24.0, help="Time window in hours")
    args = parser.parse_args()

    monitor = PerformanceMonitor({"database_path": args.db})
    summary = monitor.get_performance_summary(hours=args.hours)

    print("Overall:")
    overall = summary.get("overall_performance") or summary.get("overall") or {}
    if overall:
        print(f"  Executions: {overall.get('total_executions', overall.get('executions', 0))}")
        print(f"  Success rate: {overall.get('success_rate', 0):.2f}")
        print(f"  Avg time: {overall.get('avg_execution_time', 0):.3f}s")

    formats = summary.get("formats") or summary.get("by_format") or {}
    if formats:
        print("\nBy format:")
        for name, data in formats.items():
            if args.formats and name not in args.formats:
                continue
            perf = data.get("overall_performance") or data
            print(f"  {name} -> success={perf.get('success_rate', 0):.2f}, execs={perf.get('total_executions', perf.get('executions', 0))}, avg={perf.get('avg_execution_time', 0):.3f}s")


if __name__ == "__main__":
    main()


