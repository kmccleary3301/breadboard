#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any, Dict


DEFAULT_BASE_URL = "https://aristotle.harmonic.fun/api/v1"


def load_repo_env(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    for raw_line in open(path, "r", encoding="utf-8"):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


async def run(limit: int) -> Dict[str, Any]:
    load_repo_env(".env")
    try:
        import aristotlelib.api_request as api_request  # type: ignore
    except Exception as exc:
        raise RuntimeError(
            "aristotlelib is not installed. Install with: uv pip install aristotlelib (or pip install aristotlelib)"
        ) from exc

    api_key = os.environ.get("ARISTOTLE_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("ARISTOTLE_API_KEY is missing.")

    effective_base = os.environ.get("ARISTOTLE_BASE_URL", "").strip() or DEFAULT_BASE_URL
    api_request.BASE_URL = effective_base

    async with api_request.AristotleRequestClient() as client:
        response = await client.get("/project", params={"limit": int(limit)})
        payload = response.json()
        projects = payload.get("projects") if isinstance(payload, dict) else None
        project_count = len(projects) if isinstance(projects, list) else 0
        pagination_key = payload.get("pagination_key") if isinstance(payload, dict) else None
        return {
            "ok": True,
            "base_url": effective_base,
            "status_code": response.status_code,
            "project_count": project_count,
            "has_pagination_key": bool(pagination_key),
        }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1)
    args = parser.parse_args()

    try:
        result = asyncio.run(run(limit=int(args.limit)))
    except Exception as exc:
        print(f"[aristotlelib-smoke] ok=False error={exc}")
        return 1

    print(
        "[aristotlelib-smoke] "
        f"ok={result['ok']} status_code={result['status_code']} "
        f"project_count={result['project_count']} base_url={result['base_url']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
