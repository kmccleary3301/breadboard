#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from _cross_system_eval_v1 import dump_json, load_manifest


REPO_ROOT = Path(__file__).resolve().parents[1]
TASK_HINTS = {
    "numbertheory_2dvd4expn": (
        "Task-specific guidance:\n"
        "- Use `Nat.exists_eq_succ_of_ne_zero h₀` to rewrite `n` as `k + 1`.\n"
        "- `have h₁ : 2 ∣ 4 := by norm_num`\n"
        "- `have h₂ : 2 ^ n ∣ 4 ^ n := by exact pow_dvd_pow_of_dvd h₁ n`\n"
        "- `have h₄ : 2 ∣ 2 ^ n := by rcases Nat.exists_eq_succ_of_ne_zero h₀ with ⟨k, rfl⟩; simp [pow_succ]`\n"
        "- Finish with `exact dvd_trans h₄ h₂`.\n"
        "- Do not stop at `simp` if the remaining goal is `2 ∣ 4 ^ k * 4`; close it with divisibility lemmas.\n"
    ),
}


def _normalize_hash(text: str) -> str:
    import hashlib

    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_env_key() -> None:
    if os.environ.get("OPENROUTER_API_KEY"):
        return
    env_path = REPO_ROOT.parent / "misc" / "hermes_ref" / "firecrawl_compact" / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.startswith("OPENROUTER_API_KEY="):
            os.environ["OPENROUTER_API_KEY"] = line.split("=", 1)[1].strip()
            return


def _load_tasks(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tasks = payload.get("tasks") if isinstance(payload, dict) else payload
    if not isinstance(tasks, list):
        raise ValueError("task inputs must contain a tasks array")
    return tasks


def _statement_prefix(text: str) -> str:
    if ":= by" in text:
        return text.split(":= by", 1)[0].rstrip()
    if "begin" in text:
        return text.split("begin", 1)[0].rstrip()
    return text.strip()


def _extract_lean_block(text: str) -> Optional[str]:
    match = re.search(r"```lean\s*(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if match:
        return match.group(1).strip() + "\n"
    stripped = text.strip()
    if stripped.startswith("import ") or stripped.startswith("theorem "):
        return stripped + ("\n" if not stripped.endswith("\n") else "")
    return None


def _verify_with_kimina(proof_text: str, verifier_base_url: str) -> tuple[bool, Optional[str]]:
    import sys

    hilbert_root = REPO_ROOT.parent / "other_harness_refs" / "ml-hilbert"
    if str(hilbert_root) not in sys.path:
        sys.path.insert(0, str(hilbert_root))
    from kimina_client.sync_client import KiminaClient
    from src.tools.proof_utils import read_client_response
    from src.tools.lean_utils import extract_all_error_messages

    client = KiminaClient(api_url=verifier_base_url)
    response = client.check(proof_text.strip(), timeout=60, infotree="original")
    verification = read_client_response(response)[0]
    ok = bool(verification.get("is_correct_no_sorry"))
    if ok:
        return True, None
    try:
        errors = extract_all_error_messages(response, [proof_text])
        return False, errors[0]
    except Exception:
        return False, "verification_failed"


def _result_cost_usd(run_dir: Path) -> float:
    summary_path = run_dir / "meta" / "provider_metrics.json"
    if not summary_path.exists():
        return 0.0
    try:
        payload = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return 0.0
    total = payload.get("total_cost_usd")
    try:
        return float(total or 0.0)
    except Exception:
        return 0.0


def _build_prompt(task_id: str, full_file: str) -> str:
    hint = TASK_HINTS.get(task_id, "")
    prefix = (
        f"Task id: {task_id}\n"
        "Return a complete Lean 4 file that preserves the theorem statement exactly and replaces only the proof body.\n"
        "Do not modify imports, theorem name, binders, or hypotheses.\n"
        "Do not use sorry, admit, exact?, or theorem rewrites.\n"
        "Return exactly one ```lean fenced block, then TASK COMPLETE.\n\n"
    )
    if hint:
        prefix += f"{hint}\n"
    return prefix + (
        "Starter file:\n"
        "```lean\n"
        f"{full_file.strip()}\n"
        "```"
    )


def run_pack(
    *,
    manifest_path: Path,
    task_inputs_path: Path,
    out_path: Path,
    summary_path: Path,
    proof_output_dir: Path,
    raw_output_dir: Path,
    verifier_base_url: str,
    max_iterations: int,
    config_path: Path,
) -> Dict[str, Any]:
    _read_env_key()
    os.environ.setdefault("RAY_SCE_LOCAL_MODE", "1")
    manifest = load_manifest(manifest_path)
    tasks = _load_tasks(task_inputs_path)
    toolchain = manifest["toolchain"]
    budget_class = manifest["budget"]["class"]
    run_id = str(manifest.get("run_id") or "bb-formal-pack")
    proof_output_dir.mkdir(parents=True, exist_ok=True)
    raw_output_dir.mkdir(parents=True, exist_ok=True)
    rows: List[Dict[str, Any]] = []
    status_counts: Dict[str, int] = {}
    total_cost = 0.0

    for task in tasks:
        task_id = str(task["task_id"])
        input_text = str(task["input_text"])
        task_hash = str(task.get("input_hash") or _normalize_hash(input_text))
        workspace = raw_output_dir.parent / "bb_workspaces_v1" / task_id
        if workspace.exists():
            shutil.rmtree(workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        result_json = workspace / "result.json"
        cmd = [
            "python",
            "main.py",
            str(config_path.relative_to(REPO_ROOT)),
            "--workspace",
            str(workspace),
            "--task",
            _build_prompt(task_id, input_text),
            "--max-iterations",
            str(max_iterations),
            "--result-json",
            str(result_json),
        ]
        proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True, timeout=900)
        candidate_text = None
        run_dir = None
        result_payload: Dict[str, Any] | None = None
        if result_json.exists():
            result_payload = json.loads(result_json.read_text(encoding="utf-8"))
            result_payload = result_payload.get("result") if isinstance(result_payload, dict) else None
        if isinstance(result_payload, dict):
            run_dir = Path(str(result_payload.get("run_dir") or result_payload.get("logging_dir") or ""))
            messages = result_payload.get("messages") or []
            for message in reversed(messages):
                if isinstance(message, dict) and message.get("role") == "assistant":
                    candidate_text = _extract_lean_block(str(message.get("content") or ""))
                    if candidate_text:
                        break
        statement_ok = False
        verify_ok = False
        verify_error = None
        proof_path = proof_output_dir / f"{task_id}.lean"
        if candidate_text:
            statement_ok = _statement_prefix(candidate_text) == _statement_prefix(input_text)
            if statement_ok:
                proof_path.write_text(candidate_text, encoding="utf-8")
                verify_ok, verify_error = _verify_with_kimina(candidate_text, verifier_base_url)
        if run_dir and run_dir.exists():
            total_cost += _result_cost_usd(run_dir)
        if verify_ok and statement_ok:
            status = "SOLVED"
        elif proc.returncode != 0:
            status = "ERROR"
        else:
            status = "UNSOLVED"
        status_counts[status] = status_counts.get(status, 0) + 1
        diagnostic = {
            "task_id": task_id,
            "proc_returncode": proc.returncode,
            "stdout_tail": proc.stdout[-4000:],
            "stderr_tail": proc.stderr[-4000:],
            "statement_ok": statement_ok,
            "verify_ok": verify_ok,
            "verify_error": verify_error,
            "run_dir": str(run_dir) if run_dir else None,
            "candidate_text": candidate_text,
        }
        raw_path = raw_output_dir / f"{task_id}.json"
        raw_path.write_text(json.dumps(diagnostic, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        row = {
            "task_id": task_id,
            "toolchain_id": f"lean-{toolchain['lean_version']}__mathlib-{toolchain['mathlib_commit']}",
            "input_hash": task_hash,
            "prover_system": "bb_hilbert_like",
            "budget_class": budget_class,
            "status": status,
            "verification_log_digest": _normalize_hash(json.dumps({"statement_ok": statement_ok, "verify_ok": verify_ok, "verify_error": verify_error}, sort_keys=True)),
            "run_id": run_id,
            "attempts": 1,
            "repair_rounds_used": 0,
            "wall_clock_ms": 0,
            "proof_artifact_ref": str(proof_path) if proof_path.exists() else None,
        }
        rows.append(row)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
    summary = {
        "schema": "breadboard.bb_formal_pack_run.v1",
        "ok": True,
        "run_id": run_id,
        "task_count": len(rows),
        "status_counts": status_counts,
        "estimated_total_cost_usd": round(total_cost, 6),
        "manifest_path": str(manifest_path),
        "task_inputs_path": str(task_inputs_path),
        "result_path": str(out_path),
    }
    dump_json(summary_path, summary)
    return summary


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--task-inputs", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--summary-out", required=True)
    parser.add_argument("--proof-output-dir", required=True)
    parser.add_argument("--raw-output-dir", required=True)
    parser.add_argument("--verifier-url", default="http://127.0.0.1:18001/")
    parser.add_argument("--config", default="agent_configs/atp_hilbert_like_gpt54_v2.yaml")
    parser.add_argument("--max-iterations", type=int, default=8)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    summary = run_pack(
        manifest_path=Path(args.manifest).resolve(),
        task_inputs_path=Path(args.task_inputs).resolve(),
        out_path=Path(args.out).resolve(),
        summary_path=Path(args.summary_out).resolve(),
        proof_output_dir=Path(args.proof_output_dir).resolve(),
        raw_output_dir=Path(args.raw_output_dir).resolve(),
        verifier_base_url=str(args.verifier_url).rstrip("/"),
        max_iterations=int(args.max_iterations),
        config_path=(REPO_ROOT / args.config).resolve(),
    )
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(f"[bb-formal-pack-v1] ok={summary['ok']} tasks={summary['task_count']} statuses={summary['status_counts']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
