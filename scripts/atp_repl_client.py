"""Minimal client for the CLI bridge ATP REPL endpoint."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


def _build_request(args: argparse.Namespace) -> tuple[str, bytes, dict[str, str]]:
    base_url = args.base_url.rstrip("/")
    url = urllib.parse.urljoin(f"{base_url}/", "atp/repl")
    payload: dict[str, object] = {"commands": args.commands, "timeout_s": float(args.timeout_s)}
    if args.state_ref:
        payload["state_ref"] = args.state_ref
    if args.want_state:
        payload["want_state"] = True
    data = json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"
    return url, data, headers


def main() -> int:
    parser = argparse.ArgumentParser(description="Call the ATP REPL CLI bridge endpoint.")
    parser.add_argument(
        "commands",
        nargs="+",
        help="Lean commands to execute sequentially.",
    )
    parser.add_argument(
        "--base-url",
        default=os.environ.get("BREADBOARD_BASE_URL", "http://localhost:8000"),
        help="CLI bridge base URL (default: http://localhost:8000).",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=float(os.environ.get("ATP_REPL_TIMEOUT_S", "30")),
        help="Per-command timeout in seconds.",
    )
    parser.add_argument(
        "--state-ref",
        default=os.environ.get("ATP_REPL_STATE_REF", ""),
        help="Optional CAS reference to restore before executing commands.",
    )
    parser.add_argument(
        "--want-state",
        action="store_true",
        help="Request a new_state_ref in the response.",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("BREADBOARD_API_TOKEN", ""),
        help="Bearer token for the CLI bridge.",
    )
    parser.add_argument(
        "--pretty",
        action="store_true",
        help="Pretty-print JSON response.",
    )
    args = parser.parse_args()

    url, data, headers = _build_request(args)
    request = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=max(1.0, args.timeout_s + 5.0)) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8") if exc.fp else str(exc)
        sys.stderr.write(f"HTTP {exc.code} {exc.reason}: {body}\n")
        return 1
    except urllib.error.URLError as exc:
        sys.stderr.write(f"Request failed: {exc}\n")
        return 1

    if args.pretty:
        parsed = json.loads(body)
        print(json.dumps(parsed, indent=2, sort_keys=True))
    else:
        print(body)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
