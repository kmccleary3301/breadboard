#!/usr/bin/env python3
"""Exercise the public Python SDK against a running BreadBoard server."""

from __future__ import annotations

import json
import os

from breadboard_sdk import BreadBoardClient


DEFAULT_BASE_URL = "http://127.0.0.1:9099"


def main() -> int:
    base_url = os.environ.get("BREADBOARD_BASE_URL", DEFAULT_BASE_URL)
    auth_token = os.environ.get("BREADBOARD_API_TOKEN") or None
    client = BreadBoardClient(base_url=base_url, auth_token=auth_token)
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
