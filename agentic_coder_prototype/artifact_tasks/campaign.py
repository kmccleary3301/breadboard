from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from .runner import ArtifactTaskResult, ArtifactTaskSpec, run_artifact_task


@dataclass(frozen=True)
class CampaignCandidateSpec:
    candidate_id: str
    response_text: str = ""
    response_file: str | None = None
    task_text: str = ""
    task_file: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.candidate_id or "").strip():
            raise ValueError("candidate_id must be non-empty")
        object.__setattr__(self, "metadata", dict(self.metadata or {}))


@dataclass(frozen=True)
class CampaignSpec:
    campaign_id: str
    task_id: str
    out_dir: str
    artifact_contract: Mapping[str, Any]
    materialization: Mapping[str, Any] | None
    candidates: Sequence[CampaignCandidateSpec | Mapping[str, Any]]
    evaluators: Sequence[Mapping[str, Any]] = ()
    workspace_root: str | None = None
    resume: bool = True
    retry_failed: bool = False
    max_parallel: int = 1
    route: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.campaign_id or "").strip():
            raise ValueError("campaign_id must be non-empty")
        candidates = tuple(
            item if isinstance(item, CampaignCandidateSpec) else CampaignCandidateSpec(**dict(item))
            for item in self.candidates
        )
        if not candidates:
            raise ValueError("candidates must be non-empty")
        if int(self.max_parallel) < 1:
            raise ValueError("max_parallel must be >= 1")
        object.__setattr__(self, "candidates", candidates)
        object.__setattr__(self, "max_parallel", int(self.max_parallel))
        object.__setattr__(self, "route", dict(self.route or {}))


@dataclass(frozen=True)
class CampaignSummary:
    campaign_id: str
    status: str
    out_dir: str
    ledger_path: str
    result_count: int
    passed_count: int
    failed_count: int
    skipped_count: int
    results: tuple[Dict[str, Any], ...]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "campaign_id": self.campaign_id,
            "status": self.status,
            "out_dir": self.out_dir,
            "ledger_path": self.ledger_path,
            "result_count": self.result_count,
            "passed_count": self.passed_count,
            "failed_count": self.failed_count,
            "skipped_count": self.skipped_count,
            "results": [dict(item) for item in self.results],
        }


def _write_jsonl(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(dict(row), sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _load_ledger(path: Path) -> dict[str, Dict[str, Any]]:
    if not path.exists():
        return {}
    rows: dict[str, Dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        candidate_id = row.get("candidate_id")
        if candidate_id:
            rows[str(candidate_id)] = row
    return rows


def _run_candidate(spec: CampaignSpec, out_dir: Path, candidate: CampaignCandidateSpec) -> Dict[str, Any]:
    candidate_out = out_dir / candidate.candidate_id
    workspace = str(Path(spec.workspace_root).resolve() / candidate.candidate_id) if spec.workspace_root else None
    result: ArtifactTaskResult = run_artifact_task(
        ArtifactTaskSpec(
            task_id=spec.task_id,
            candidate_id=candidate.candidate_id,
            task_text=candidate.task_text,
            task_file=candidate.task_file,
            response_text=candidate.response_text,
            response_file=candidate.response_file,
            artifact_contract=spec.artifact_contract,
            materialization=spec.materialization,
            evaluators=spec.evaluators,
            workspace_root=workspace,
            out_dir=str(candidate_out),
            route={**dict(spec.route), "campaign_id": spec.campaign_id},
            notes={"candidate_metadata": dict(candidate.metadata)},
        )
    )
    return {
        "campaign_id": spec.campaign_id,
        "task_id": spec.task_id,
        "candidate_id": candidate.candidate_id,
        "status": result.status,
        "ok": result.ok,
        "failure_reasons": list(result.failure_reasons),
        "manifest_path": result.evidence_manifest.manifest_path,
        "campaign_action": "ran",
    }


def run_campaign(spec: CampaignSpec) -> CampaignSummary:
    out_dir = Path(spec.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    ledger_path = out_dir / "campaign_ledger.jsonl"
    existing = _load_ledger(ledger_path) if spec.resume else {}
    final_by_candidate: dict[str, Dict[str, Any]] = {}
    to_run: list[CampaignCandidateSpec] = []

    for candidate in spec.candidates:
        existing_row = existing.get(candidate.candidate_id)
        if existing_row and existing_row.get("status") == "passed" and not spec.retry_failed:
            skipped = dict(existing_row)
            skipped["campaign_action"] = "skipped_existing_passed"
            final_by_candidate[candidate.candidate_id] = skipped
            continue
        if existing_row and existing_row.get("status") == "failed" and not spec.retry_failed:
            skipped = dict(existing_row)
            skipped["campaign_action"] = "skipped_existing_failed"
            final_by_candidate[candidate.candidate_id] = skipped
            continue
        to_run.append(candidate)

    if spec.max_parallel == 1 or len(to_run) <= 1:
        for candidate in to_run:
            final_by_candidate[candidate.candidate_id] = _run_candidate(spec, out_dir, candidate)
            _write_jsonl(ledger_path, list(final_by_candidate.values()))
    else:
        with ThreadPoolExecutor(max_workers=spec.max_parallel) as pool:
            futures = {pool.submit(_run_candidate, spec, out_dir, candidate): candidate for candidate in to_run}
            for future in as_completed(futures):
                candidate = futures[future]
                final_by_candidate[candidate.candidate_id] = future.result()
                _write_jsonl(ledger_path, list(final_by_candidate.values()))

    final_rows = tuple(final_by_candidate[candidate.candidate_id] for candidate in spec.candidates if candidate.candidate_id in final_by_candidate)
    passed = sum(1 for row in final_rows if row.get("status") == "passed" and str(row.get("campaign_action", "")).startswith(("ran", "skipped")))
    failed = sum(1 for row in final_rows if row.get("status") == "failed" and str(row.get("campaign_action", "")).startswith(("ran", "skipped")))
    skipped = sum(1 for row in final_rows if str(row.get("campaign_action", "")).startswith("skipped"))
    summary = CampaignSummary(
        campaign_id=spec.campaign_id,
        status="passed" if failed == 0 else "failed",
        out_dir=str(out_dir),
        ledger_path=str(ledger_path),
        result_count=len(final_rows),
        passed_count=passed,
        failed_count=failed,
        skipped_count=skipped,
        results=final_rows,
    )
    (out_dir / "campaign_summary.json").write_text(json.dumps(summary.to_dict(), indent=2, sort_keys=True), encoding="utf-8")
    _write_jsonl(ledger_path, final_rows)
    return summary
