#!/usr/bin/env python3
"""
Utility to inspect and clean CLI bridge sessions.

Examples:
    python scripts/cli_session_health.py --base-url http://127.0.0.1:9099 --fail-on-policy
    python scripts/cli_session_health.py --cleanup
"""
from __future__ import annotations

import argparse
import sys
from typing import Any, Dict, List

import requests


def fetch_sessions(base_url: str) -> List[Dict[str, Any]]:
    resp = requests.get(f"{base_url}/sessions", timeout=5)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, list) else []


def fetch_summary(base_url: str, session_id: str) -> Dict[str, Any]:
    resp = requests.get(f"{base_url}/sessions/{session_id}", timeout=5)
    resp.raise_for_status()
    data = resp.json()
    return data if isinstance(data, dict) else {}


def delete_session(base_url: str, session_id: str) -> None:
    resp = requests.delete(f"{base_url}/sessions/{session_id}", timeout=5)
    if resp.status_code not in (200, 202, 204, 404):
        resp.raise_for_status()


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect and clean BreadBoard CLI sessions.")
    parser.add_argument("--base-url", default="http://127.0.0.1:9099", help="FastAPI bridge base URL.")
    parser.add_argument(
        "--fail-on-policy",
        action="store_true",
        help="Exit non-zero if any session completed with method=policy_violation.",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete every session after inspection to keep the registry clean.",
    )
    args = parser.parse_args()

    sessions = fetch_sessions(args.base_url)
    if not sessions:
        print("No active sessions.")
        return

    print(f"Found {len(sessions)} session(s):")
    violations: List[str] = []
    for summary in sessions:
        session_id = summary.get("session_id")
        status = summary.get("status")
        print(f"- {session_id} status={status}")
        details = fetch_summary(args.base_url, session_id)
        completion = details.get("completion_summary") or {}
        method = completion.get("method") or completion.get("reason")
        if method:
            print(f"  completion.method={method}")
        if args.fail-on-policy and method == "policy_violation":
            violations.append(session_id)

        if args.cleanup:
            delete_session(args.base_url, session_id)
            print("  deleted")

    if args.fail-on-policy and violations:
        print(f"Policy violations detected for sessions: {', '.join(violations)}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
