#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict

ROOT_DIR = Path(__file__).resolve().parents[2]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from agentic_coder_prototype.artifact_tasks import (  # noqa: E402
    ArtifactContract,
    ArtifactRequirement,
    ArtifactTaskSpec,
    EvaluatorSpec,
    MaterializationSpec,
    run_artifact_task,
)


def _read_text_arg(value: str | None, file_path: str | None, *, default: str) -> str:
    if file_path:
        return Path(file_path).read_text(encoding="utf-8")
    if value is not None:
        return value
    return default


def _parse_evaluator(values: list[str]) -> list[EvaluatorSpec]:
    specs: list[EvaluatorSpec] = []
    for index, raw in enumerate(values):
        name = f"eval_{index}"
        command = raw
        if "::" in raw:
            name, command = raw.split("::", 1)
        specs.append(EvaluatorSpec(name=name, command=command, timeout_seconds=30.0))
    return specs


def main() -> int:
    parser = argparse.ArgumentParser(description="Run an offline artifact-task smoke without provider calls.")
    parser.add_argument("--task-id", default="artifact_task_smoke")
    parser.add_argument("--candidate-id", default="candidate_0001")
    parser.add_argument("--task-text")
    parser.add_argument("--task-file")
    parser.add_argument("--response-text")
    parser.add_argument("--response-file")
    parser.add_argument("--artifact-path", default="candidate.py")
    parser.add_argument("--language", default="python")
    parser.add_argument("--min-bytes", type=int, default=1)
    parser.add_argument("--workspace-root")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--evaluator", action="append", default=[], help="Optional NAME::COMMAND or COMMAND evaluator.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    args = parser.parse_args()

    default_task = "Produce exactly one Python artifact."
    default_response = "```python\nprint('artifact smoke')\n```\n"
    task_text = _read_text_arg(args.task_text, args.task_file, default=default_task)
    response_text = _read_text_arg(args.response_text, args.response_file, default=default_response)

    contract = ArtifactContract(
        requirements=[ArtifactRequirement(path=args.artifact_path, min_bytes=args.min_bytes)],
        mode="response_materialize",
    )
    result = run_artifact_task(
        ArtifactTaskSpec(
            task_id=args.task_id,
            candidate_id=args.candidate_id,
            task_text=task_text,
            response_text=response_text,
            artifact_contract=contract,
            materialization=MaterializationSpec(language=args.language, output_path=args.artifact_path),
            evaluators=_parse_evaluator(args.evaluator),
            workspace_root=args.workspace_root,
            out_dir=args.out_dir,
            route={"operator": "scripts/dev/artifact_task_smoke.py"},
        )
    )
    payload: Dict[str, Any] = result.to_dict()
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        print(f"[artifact-task-smoke] status={result.status} manifest={result.evidence_manifest.manifest_path}")
        if result.failure_reasons:
            print("[artifact-task-smoke] failures=" + ", ".join(result.failure_reasons), file=sys.stderr)
    return 0 if result.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
