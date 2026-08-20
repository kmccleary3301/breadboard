from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Dict, Iterable, List

try:
    import yaml
except Exception:  # pragma: no cover
    yaml = None


VALID_RESULT_STATUSES = {"SOLVED", "UNSOLVED", "ERROR", "TIMEOUT"}


def load_manifest(path: Path) -> Dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() in {".yaml", ".yml"}:
        if yaml is None:
            raise RuntimeError("PyYAML is required to parse YAML manifests")
        payload = yaml.safe_load(text)
    else:
        payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError(f"manifest must be an object: {path}")
    return payload


def dump_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def load_jsonl(paths: Iterable[Path]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"result file missing: {path}")
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            payload = json.loads(line)
            if not isinstance(payload, dict):
                raise ValueError(f"result row must be object at {path}:{line_no}")
            payload["_source_path"] = str(path)
            payload["_source_line"] = line_no
            rows.append(payload)
    return rows


def validate_manifest_shape(manifest: Dict[str, Any]) -> List[str]:
    errors: List[str] = []
    for key in ("run_id", "created_at_utc", "owner", "purpose"):
        if not str(manifest.get(key) or "").strip():
            errors.append(f"manifest.{key} missing")
    benchmark = manifest.get("benchmark")
    if not isinstance(benchmark, dict):
        errors.append("manifest.benchmark missing or invalid")
        benchmark = {}
    slice_block = benchmark.get("slice")
    if not isinstance(slice_block, dict):
        errors.append("manifest.benchmark.slice missing or invalid")
        slice_block = {}
    task_ids_raw = slice_block.get("task_ids")
    if not isinstance(task_ids_raw, list) or not task_ids_raw:
        errors.append("manifest.benchmark.slice.task_ids must be non-empty list")
    n_tasks = slice_block.get("n_tasks")
    if not isinstance(n_tasks, int) or n_tasks <= 0:
        errors.append("manifest.benchmark.slice.n_tasks must be integer > 0")
    systems = manifest.get("systems")
    if not isinstance(systems, list) or not systems:
        errors.append("manifest.systems must be non-empty list")
    return errors


def required_result_fields(manifest: Dict[str, Any]) -> List[str]:
    base = [
        "task_id",
        "toolchain_id",
        "input_hash",
        "prover_system",
        "budget_class",
        "status",
        "verification_log_digest",
    ]
    acceptance = manifest.get("acceptance")
    required_fields = acceptance.get("required_fields") if isinstance(acceptance, dict) else []
    if isinstance(required_fields, list):
        for item in required_fields:
            value = str(item).strip()
            if value and value not in base:
                base.append(value)
    return base


def solve_bool(status: Any) -> bool:
    return str(status or "").strip().upper() == "SOLVED"


def wilson_interval(successes: int, total: int, z: float = 1.96) -> Dict[str, float]:
    if total <= 0:
        return {"low": 0.0, "high": 0.0}
    phat = float(successes) / float(total)
    z2 = z * z
    denom = 1.0 + (z2 / float(total))
    center = (phat + (z2 / (2.0 * float(total)))) / denom
    half = (z * math.sqrt((phat * (1.0 - phat) + (z2 / (4.0 * float(total)))) / float(total))) / denom
    return {"low": max(0.0, center - half), "high": min(1.0, center + half)}


def exact_mcnemar_pvalue(cand_wins: int, base_wins: int) -> float:
    discordant = int(cand_wins) + int(base_wins)
    if discordant <= 0:
        return 1.0
    smaller = min(int(cand_wins), int(base_wins))
    cdf = 0.0
    for i in range(0, smaller + 1):
        cdf += math.comb(discordant, i) * (0.5 ** discordant)
    return float(min(1.0, 2.0 * cdf))
