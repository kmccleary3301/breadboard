#!/usr/bin/env python3
"""Exercise the public Python SDK against a running BreadBoard server."""

from __future__ import annotations

import json
import math
import os

from breadboard_sdk import BreadBoardClient


DEFAULT_BASE_URL = "http://127.0.0.1:9099"


def main() -> int:
    base_url = os.environ.get("BREADBOARD_BASE_URL") or DEFAULT_BASE_URL
    auth_token = os.environ.get("BREADBOARD_API_TOKEN") or None
    timeout_raw = os.environ.get("BREADBOARD_SDK_TIMEOUT_S") or "30"
    try:
        timeout_s = float(timeout_raw)
    except ValueError as error:
        raise SystemExit("BREADBOARD_SDK_TIMEOUT_S must be a positive number") from error
    if not math.isfinite(timeout_s) or timeout_s <= 0:
        raise SystemExit("BREADBOARD_SDK_TIMEOUT_S must be a positive number")
    client = BreadBoardClient(
        base_url=base_url,
        auth_token=auth_token,
        timeout_s=timeout_s,
    )
    result = client.health_system()
    if result.get("ok") is not True:
        raise SystemExit(
            "Python SDK health check failed: "
            + json.dumps(result, sort_keys=True)
        )
    print(f"[sdk-python] ok ({base_url})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
