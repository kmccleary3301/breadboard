#!/usr/bin/env python3
"""
Verify there is a successful tmux-phase4-fullpane-gate workflow run for a commit SHA.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_WORKFLOW_FILE = "tmux-phase4-fullpane-gate.yml"


def _api_get(url: str, token: str) -> dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "verify-phase4-gate-run-for-sha",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("GitHub API response is not a JSON object")
    return payload


def select_latest_successful_run(payload: dict[str, Any]) -> dict[str, Any] | None:
    runs = payload.get("workflow_runs")
    if not isinstance(runs, list):
        return None
    success: list[dict[str, Any]] = []
    for item in runs:
        if isinstance(item, dict) and item.get("conclusion") == "success":
            success.append(item)
    if not success:
        return None
    success.sort(key=lambda r: str(r.get("updated_at") or ""), reverse=True)
    return success[0]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Verify strict phase4 gate run exists for a commit SHA")
    p.add_argument("--sha", required=True)
    p.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    p.add_argument("--workflow-file", default=DEFAULT_WORKFLOW_FILE)
    p.add_argument("--token", default=os.environ.get("GITHUB_TOKEN", ""))
    p.add_argument("--output-json", default="")
    p.add_argument("--env-file", default="")
    return p.parse_args()


def main() -> int:
    try:
        args = parse_args()
        repo = str(args.repo or "").strip()
        token = str(args.token or "").strip()
        sha = str(args.sha or "").strip()
        if not repo:
            raise ValueError("--repo is required (or set GITHUB_REPOSITORY)")
        if not token:
            raise ValueError("--token is required (or set GITHUB_TOKEN)")
        if not sha:
            raise ValueError("--sha is required")

        query = urllib.parse.urlencode({"head_sha": sha, "status": "completed", "per_page": 50})
        url = f"https://api.github.com/repos/{repo}/actions/workflows/{args.workflow_file}/runs?{query}"
        payload = _api_get(url, token)
        best = select_latest_successful_run(payload)

        result: dict[str, Any] = {
            "ok": best is not None,
            "repo": repo,
            "sha": sha,
            "workflow_file": args.workflow_file,
            "run": {
                "id": None,
                "url": None,
                "updated_at": None,
            },
        }
        if best is not None:
            result["run"] = {
                "id": best.get("id"),
                "url": best.get("html_url"),
                "updated_at": best.get("updated_at"),
            }

        if args.output_json:
            out = Path(args.output_json).expanduser().resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")

        if args.env_file:
            env_path = Path(args.env_file).expanduser().resolve()
            env_path.parent.mkdir(parents=True, exist_ok=True)
            with env_path.open("a", encoding="utf-8") as f:
                f.write(f"GATE_RUN_ID={result['run']['id'] or ''}\n")
                f.write(f"GATE_RUN_URL={result['run']['url'] or ''}\n")

        if result["ok"]:
            print(
                f"[phase4-preflight] pass: found successful strict gate run id={result['run']['id']} sha={sha}"
            )
            return 0

        print(
            f"[phase4-preflight] fail: no successful '{args.workflow_file}' run for commit {sha}. "
            "Run strict gate first, then cut release tag."
        )
        return 2
    except SystemExit:
        raise
    except Exception as exc:
        print(f"[phase4-preflight] error: {exc}")
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
