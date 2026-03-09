#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import requests

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from _cross_system_eval_v1 import dump_json, load_manifest
from breadboard_sdk.client import BreadboardClient

try:
    from breadboard_ext.atp.aristotle_adapter import build_toolchain_id, normalize_input_hash
except Exception:
    def normalize_input_hash(input_text: str) -> str:
        normalized = " ".join(str(input_text).split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def build_toolchain_id(lean_version: str, mathlib_commit: str) -> str:
        version = str(lean_version).strip()
        commit = str(mathlib_commit).strip()
        return f"lean{version}_mathlib.{commit}"


_TERMINAL_SESSION_STATUSES = {"completed", "failed", "stopped"}


@dataclass(frozen=True)
class PreparedTask:
    task_id: str
    input_text: str
    input_hash: str
    workspace_dir: Path
    target_path: Path
    notes_path: Path
    diagnostic_path: Path
    prompt: str


@dataclass(frozen=True)
class TaskExecutionResult:
    session_id: str
    session_status: str
    wall_clock_ms: int
    logging_dir: str | None
    timed_out: bool
    completion_summary: dict[str, Any] | None
    reward_summary: dict[str, Any] | None
    metadata: dict[str, Any] | None


def _canonical_digest(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _safe_relpath(path: Path, *, start: Path) -> str:
    with contextlib.suppress(Exception):
        return str(path.resolve().relative_to(start.resolve()))
    return str(path.resolve())


def _load_task_inputs(path: Path) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    tasks_raw: list[Any]
    if isinstance(payload, dict):
        tasks_raw = payload.get("tasks") or []
    elif isinstance(payload, list):
        tasks_raw = payload
    else:
        raise ValueError("task inputs payload must be JSON object or array")
    if not isinstance(tasks_raw, list) or not tasks_raw:
        raise ValueError("task inputs must include non-empty `tasks` array")

    tasks: list[dict[str, str]] = []
    for index, row in enumerate(tasks_raw):
        if not isinstance(row, dict):
            raise ValueError(f"tasks[{index}] must be object")
        task_id = str(row.get("task_id") or "").strip()
        input_text = str(row.get("input_text") or "").strip()
        input_mode = str(row.get("input_mode") or "formal_lean").strip()
        input_hash = str(row.get("input_hash") or "").strip()
        if not task_id:
            raise ValueError(f"tasks[{index}] missing task_id")
        if not input_text:
            raise ValueError(f"tasks[{index}] missing input_text")
        if input_mode != "formal_lean":
            raise ValueError(f"tasks[{index}] unsupported input_mode={input_mode!r}")
        tasks.append(
            {
                "task_id": task_id,
                "input_text": input_text,
                "input_mode": input_mode,
                "input_hash": input_hash,
            }
        )
    return tasks


def _manifest_toolchain_id(manifest: dict[str, Any]) -> str:
    toolchain = manifest.get("toolchain")
    if not isinstance(toolchain, dict):
        raise ValueError("manifest.toolchain missing")
    lean_version = str(toolchain.get("lean_version") or "").strip()
    mathlib_commit = str(toolchain.get("mathlib_commit") or "").strip()
    if not lean_version or not mathlib_commit:
        raise ValueError("manifest.toolchain requires lean_version + mathlib_commit")
    return build_toolchain_id(lean_version=lean_version, mathlib_commit=mathlib_commit)


def _manifest_budget_class(manifest: dict[str, Any]) -> str:
    budget = manifest.get("budget")
    if not isinstance(budget, dict):
        raise ValueError("manifest.budget missing")
    budget_class = str(budget.get("class") or "").strip()
    if not budget_class:
        raise ValueError("manifest.budget.class missing")
    return budget_class


def _manifest_wall_clock_cap_s(manifest: dict[str, Any]) -> int:
    budget = manifest.get("budget")
    if not isinstance(budget, dict):
        return 300
    try:
        value = int(budget.get("wall_clock_cap_s") or 300)
    except Exception:
        value = 300
    return max(30, value)


def _manifest_system_config_ref(manifest: dict[str, Any], *, system_id: str) -> str:
    systems = manifest.get("systems")
    if not isinstance(systems, list):
        return ""
    for row in systems:
        if not isinstance(row, dict):
            continue
        if str(row.get("system_id") or "").strip() != str(system_id).strip():
            continue
        return str(row.get("config_ref") or "").strip()
    return ""


def _resolve_artifact_dirs(
    manifest: dict[str, Any],
    *,
    system_id: str,
    proof_output_dir: str | None,
    raw_output_dir: str | None,
    workspace_root: str | None,
) -> tuple[Path, Path, Path]:
    artifacts_block = manifest.get("artifacts")
    root = None
    if isinstance(artifacts_block, dict):
        candidate = str(artifacts_block.get("root_dir") or "").strip()
        if candidate:
            root = Path(candidate).resolve()
    if root is None:
        root = (REPO_ROOT / "artifacts" / "benchmarks").resolve()

    effective_raw = Path(raw_output_dir).resolve() if raw_output_dir else root / "cross_system" / system_id / "raw"
    effective_proof = Path(proof_output_dir).resolve() if proof_output_dir else root / "cross_system" / system_id / "proofs"
    effective_workspace = (
        Path(workspace_root).resolve()
        if workspace_root
        else root / "cross_system" / system_id / "workspaces"
    )
    return effective_proof, effective_raw, effective_workspace


def _resolve_config_path(config_path: str) -> Path:
    candidate = Path(config_path).expanduser()
    if candidate.is_file():
        return candidate.resolve()
    repo_candidate = (REPO_ROOT / candidate).resolve()
    if repo_candidate.is_file():
        return repo_candidate
    raise FileNotFoundError(f"config file not found: {config_path}")


def _task_specific_guidance(task_id: str) -> str:
    normalized = str(task_id).strip().lower()
    if normalized == "imo_1977_p6":
        return textwrap.dedent(
            """\
            Task-specific proof hints:
            - First prove `StrictMono f` from `h₀`; this should give `a < b -> f a < f b`.
            - Use monotonicity to derive the lower bound `n ≤ f n` for every positive natural `n`.
            - Prove the base case `f 1 = 1` by contradiction using the lower bound and `h₀ 1`.
            - Then aim for induction on positive naturals; `h₀` should relate `f (f n)` and `f (n + 1)`.
            - Prefer named lemmas and short structured steps over repeated search commands.
            """
        ).rstrip()
    if normalized == "mathd_numbertheory_780":
        return textwrap.dedent(
            """\
            Task-specific proof hints:
            - Avoid leaving `aesop` as the final proof. Use it only for quick probing, then replace it with an explicit argument.
            - The intended number-theory shape is: if `x = 6⁻¹` modulo `m` and also `x ≡ 36 [MOD m]`, then `6 * 36 ≡ 1 [MOD m]`.
            - From that, derive that `m` divides `215`, then combine with `10 ≤ m ≤ 99` to conclude `m = 43`.
            - Tactics worth trying after introducing the arithmetic facts: `norm_num`, `omega`, `linarith`, `nlinarith`, `zify`.
            - If the current statement shape around `x` is awkward, add intermediate `have` statements instead of brute-force search.
            """
        ).rstrip()
    return textwrap.dedent(
        """\
        Task-specific proof hints:
        - Prefer explicit `have` lemmas and short structured rewrites over repeated directory inspection.
        - If `aesop` fails, replace it with a more explicit proof attempt; do not keep the same tactic unchanged.
        - After each verifier failure, edit the theorem body immediately using the new feedback.
        """
    ).rstrip()


def _build_task_prompt(*, prepared: PreparedTask, verifier_url: str) -> str:
    rel_target = _safe_relpath(prepared.target_path, start=prepared.workspace_dir)
    rel_notes = _safe_relpath(prepared.notes_path, start=prepared.workspace_dir)
    theorem_block = prepared.input_text.rstrip()
    hint_block = _task_specific_guidance(prepared.task_id)
    return textwrap.dedent(
        f"""\
        You are solving a Lean 4 theorem in the current workspace.

        Requirements:
        1. Start by creating `target/`, `artifacts/`, and `result/`, then write `{rel_target}` with a concrete proof attempt.
           Your first tool call must modify `{rel_target}` directly with a whole-file rewrite command. Use this exact template:
           ```bash
           mkdir -p target artifacts result && python - <<'PY'
           from pathlib import Path
           Path("{rel_target}").write_text(\"\"\"{theorem_block}
           \"\"\", encoding="utf-8")
           PY
           ```
           Fill in the proof body before running it. Do not leave the file unchanged.
        2. Preserve the theorem statement and imports unless a minimal import change is strictly required.
        3. Use the verifier until the file is clean. After each edit, run this exact command:
           ```bash
           python - <<'PY'
           import pathlib
           import requests
           proof_path = pathlib.Path("{rel_target}")
           payload = {{
               "codes": [{{"custom_id": "{prepared.task_id}", "proof": proof_path.read_text(encoding="utf-8")}}],
               "timeout": 180,
           }}
           response = requests.post("{verifier_url}", json=payload, timeout=240)
           response.raise_for_status()
           pathlib.Path("artifacts").mkdir(parents=True, exist_ok=True)
           pathlib.Path("artifacts/verify_latest.json").write_text(response.text + ("\\n" if not response.text.endswith("\\n") else ""), encoding="utf-8")
           print(response.text)
           PY
           ```
        4. After each verification attempt, inspect `artifacts/verify_latest.json` and make another proof edit. Do not spend more than one inspection/read-only shell command between proof edits.
        5. If you cannot solve the theorem within budget, leave your best attempt in place and explain the blocker in `{rel_notes}`.

        Work cleanly:
        - Alternate between proof edits and verification.
        - Read-only shell commands are only for verifier feedback or a single targeted inspection; avoid directory listing loops.
        - Do not create alternate theorem files.
        - Do not call `mark_task_complete` unless `{rel_target}` exists and the verifier has passed cleanly.
        - When the verifier passes with no `sorry` and no errors, stop and reply with exactly `TASK COMPLETE`.

        {hint_block}

        Verifier endpoint for this workspace: `{verifier_url}`
        """
    )


def prepare_task_workspace(
    *,
    task: dict[str, str],
    workspace_root: Path,
    verifier_url: str,
) -> PreparedTask:
    task_id = str(task["task_id"]).strip()
    task_input = str(task["input_text"])
    task_hash = str(task.get("input_hash") or "").strip() or normalize_input_hash(task_input)
    task_dir = workspace_root / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    target_path = task_dir / "target" / f"{task_id}.lean"
    notes_path = task_dir / "result" / "notes.md"
    diagnostic_path = task_dir / "artifacts" / "runner_diagnostic.json"
    target_path.parent.mkdir(parents=True, exist_ok=True)
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
    prompt = _build_task_prompt(
        prepared=PreparedTask(
            task_id=task_id,
            input_text=task_input,
            input_hash=task_hash,
            workspace_dir=task_dir,
            target_path=target_path,
            notes_path=notes_path,
            diagnostic_path=diagnostic_path,
            prompt="",
        ),
        verifier_url=verifier_url,
    )
    return PreparedTask(
        task_id=task_id,
        input_text=task_input,
        input_hash=task_hash,
        workspace_dir=task_dir,
        target_path=target_path,
        notes_path=notes_path,
        diagnostic_path=diagnostic_path,
        prompt=prompt,
    )


def _try_health(base_url: str, *, timeout_s: float = 0.6) -> bool:
    try:
        BreadboardClient(base_url=base_url, timeout_s=timeout_s).health()
        return True
    except Exception:
        return False


def _wait_for_engine(base_url: str, *, timeout_s: float = 20.0) -> None:
    deadline = time.monotonic() + max(0.0, float(timeout_s))
    while True:
        if _try_health(base_url, timeout_s=0.6):
            return
        if time.monotonic() >= deadline:
            raise RuntimeError(f"timed out waiting for engine at {base_url}")
        time.sleep(0.25)


def _start_local_engine(*, repo_root: Path, host: str, port: int, log_level: str) -> subprocess.Popen[bytes]:
    env = dict(os.environ)
    env.setdefault("BREADBOARD_CLI_HOST", host)
    env.setdefault("BREADBOARD_CLI_PORT", str(port))
    env.setdefault("BREADBOARD_CLI_LOG_LEVEL", log_level)
    env.setdefault("BREADBOARD_LOAD_DOTENV", "1")
    cmd = [sys.executable, "-m", "agentic_coder_prototype.api.cli_bridge.server"]
    return subprocess.Popen(
        cmd,
        cwd=str(repo_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


def _engine_base_url(*, host: str, port: int) -> str:
    return f"http://{host}:{int(port)}"


def _ensure_engine(
    *,
    base_url: str,
    start_engine: bool,
    repo_root: Path,
    engine_host: str,
    engine_port: int,
    engine_log_level: str,
    wait_timeout_s: float,
) -> subprocess.Popen[bytes] | None:
    if _try_health(base_url):
        return None
    if not start_engine:
        raise RuntimeError(f"engine not reachable at {base_url}")
    proc = _start_local_engine(repo_root=repo_root, host=engine_host, port=engine_port, log_level=engine_log_level)
    try:
        _wait_for_engine(base_url, timeout_s=wait_timeout_s)
    except Exception:
        with contextlib.suppress(Exception):
            proc.terminate()
        raise
    return proc


def _is_session_done(status: str) -> bool:
    return str(status or "").strip().lower() in _TERMINAL_SESSION_STATUSES


def _completion_summary_done(summary: dict[str, Any] | None) -> bool:
    if not isinstance(summary, dict):
        return False
    if bool(summary.get("completed")):
        return True
    reason = str(summary.get("reason") or "").strip().lower()
    exit_kind = str(summary.get("exit_kind") or "").strip().lower()
    method = str(summary.get("method") or "").strip().lower()
    return any(
        value in {"max_steps_exhausted", "loop_exit"}
        for value in (reason, exit_kind, method)
    )


def _run_task_with_breadboard(
    *,
    client: BreadboardClient,
    config_path: str,
    model: str | None,
    prepared: PreparedTask,
    permission_mode: str,
    timeout_s: int,
    poll_interval_s: float,
) -> TaskExecutionResult:
    metadata = {
        "runner": "bb_atp_adapter_slice_v2",
        "task_id": prepared.task_id,
    }
    if model:
        metadata["model"] = model
    started = time.monotonic()
    created = client.create_session(
        config_path=config_path,
        task=prepared.prompt,
        metadata=metadata,
        workspace=str(prepared.workspace_dir),
        max_steps=max(24, min(80, max(1, int(timeout_s)) // 10)),
        permission_mode=permission_mode,
        stream=False,
    )
    session_id = str(created.get("session_id") or "").strip()
    if not session_id:
        raise RuntimeError("breadboard session create response missing session_id")
    logging_dir = created.get("logging_dir")
    deadline = started + max(30, int(timeout_s))
    last_summary: dict[str, Any] | None = None
    timed_out = False
    session_status = str(created.get("status") or "").strip().lower() or "starting"
    while True:
        last_summary = client.get_session(session_id)
        session_status = str(last_summary.get("status") or "").strip().lower() or session_status
        completion_summary = last_summary.get("completion_summary") if isinstance(last_summary.get("completion_summary"), dict) else None
        if _is_session_done(session_status) or _completion_summary_done(completion_summary):
            break
        if time.monotonic() >= deadline:
            timed_out = True
            with contextlib.suppress(Exception):
                client.post_command(session_id, command="stop")
            time.sleep(min(2.0, max(0.1, poll_interval_s)))
            with contextlib.suppress(Exception):
                last_summary = client.get_session(session_id)
                session_status = str(last_summary.get("status") or "").strip().lower() or "stopped"
            break
        time.sleep(max(0.1, float(poll_interval_s)))

    summary = last_summary or {}
    completion_summary = summary.get("completion_summary") if isinstance(summary.get("completion_summary"), dict) else None
    reward_summary = summary.get("reward_summary") if isinstance(summary.get("reward_summary"), dict) else None
    metadata_summary = summary.get("metadata") if isinstance(summary.get("metadata"), dict) else None
    return TaskExecutionResult(
        session_id=session_id,
        session_status=session_status,
        wall_clock_ms=max(0, int((time.monotonic() - started) * 1000.0)),
        logging_dir=str(logging_dir or summary.get("logging_dir") or "").strip() or None,
        timed_out=timed_out,
        completion_summary=completion_summary,
        reward_summary=reward_summary,
        metadata=metadata_summary,
    )


def verify_with_kimina(*, proof_text: str, task_id: str, verifier_url: str, timeout_s: int) -> dict[str, Any]:
    payload = {
        "codes": [{"custom_id": task_id, "proof": proof_text}],
        "timeout": int(timeout_s),
    }
    response = requests.post(
        verifier_url,
        json=payload,
        headers={"Content-Type": "application/json"},
        timeout=max(10, int(timeout_s) + 30),
    )
    response.raise_for_status()
    return response.json()


def _interpret_verifier_payload(payload: dict[str, Any]) -> dict[str, Any]:
    rows = payload.get("results")
    if not isinstance(rows, list) or not rows:
        return {"ok": False, "is_valid_no_sorry": False, "has_error": True, "error": "missing_results"}
    first = rows[0]
    if not isinstance(first, dict):
        return {"ok": False, "is_valid_no_sorry": False, "has_error": True, "error": "invalid_result_row"}
    error_message = first.get("error")
    feedback = first.get("response") if isinstance(first.get("response"), dict) else {}
    messages = feedback.get("messages") if isinstance(feedback, dict) else []
    has_error = bool(error_message)
    has_sorry_warning = False
    if isinstance(messages, list):
        for item in messages:
            if not isinstance(item, dict):
                continue
            severity = str(item.get("severity") or "").strip().lower()
            data = str(item.get("data") or "")
            if severity == "error":
                has_error = True
            if "declaration uses 'sorry'" in data or "declaration uses 'admit'" in data:
                has_sorry_warning = True
    return {
        "ok": not has_error and not has_sorry_warning,
        "is_valid_no_sorry": not has_error and not has_sorry_warning,
        "has_error": has_error,
        "has_sorry_warning": has_sorry_warning,
        "error": str(error_message or "").strip(),
        "time": feedback.get("time") if isinstance(feedback, dict) else None,
    }


def _proof_candidate_is_present(*, task_id: str, proof_text: str) -> bool:
    text = str(proof_text or "").strip()
    if not text:
        return False
    lowered = text.lower()
    return task_id.lower() in lowered and any(token in lowered for token in ("theorem ", "lemma ", "example "))


def _status_from_execution(*, execution: TaskExecutionResult, verification: dict[str, Any], candidate_present: bool) -> str:
    if candidate_present and bool(verification.get("is_valid_no_sorry")):
        return "SOLVED"
    if execution.timed_out:
        return "TIMEOUT"
    if str(execution.session_status).lower() == "failed":
        return "ERROR"
    return "UNSOLVED"


def _run_single_task(
    *,
    manifest: dict[str, Any],
    task: dict[str, str],
    client: BreadboardClient,
    config_path: str,
    model: str | None,
    system_id: str,
    proof_dir: Path,
    raw_dir: Path,
    workspace_root: Path,
    verifier_url: str,
    verifier_timeout_s: int,
    permission_mode: str,
    task_timeout_s: int,
    poll_interval_s: float,
    task_runner: Callable[..., TaskExecutionResult] | None = None,
    verifier: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    prepared = prepare_task_workspace(task=task, workspace_root=workspace_root, verifier_url=verifier_url)
    runner = task_runner or _run_task_with_breadboard
    execution = runner(
        client=client,
        config_path=config_path,
        model=model,
        prepared=prepared,
        permission_mode=permission_mode,
        timeout_s=task_timeout_s,
        poll_interval_s=poll_interval_s,
    )

    proof_path = prepared.target_path
    proof_text = proof_path.read_text(encoding="utf-8") if proof_path.exists() else ""
    candidate_present = _proof_candidate_is_present(task_id=prepared.task_id, proof_text=proof_text)
    verify_fn = verifier or verify_with_kimina
    verifier_payload: dict[str, Any]
    verifier_error = ""
    try:
        verifier_payload = verify_fn(
            proof_text=proof_text,
            task_id=prepared.task_id,
            verifier_url=verifier_url,
            timeout_s=verifier_timeout_s,
        )
    except Exception as exc:
        verifier_payload = {"error": str(exc), "results": []}
        verifier_error = str(exc)
    verification = _interpret_verifier_payload(verifier_payload)
    if not candidate_present and not verifier_error:
        verifier_error = "missing_or_invalid_candidate_proof"
    status = _status_from_execution(execution=execution, verification=verification, candidate_present=candidate_present)

    diagnostic_payload = {
        "schema": "breadboard.bb_atp_adapter_slice_v2.task_diagnostic",
        "task_id": prepared.task_id,
        "session_id": execution.session_id,
        "session_status": execution.session_status,
        "timed_out": execution.timed_out,
        "logging_dir": execution.logging_dir,
        "workspace_dir": str(prepared.workspace_dir),
        "target_path": str(prepared.target_path),
        "notes_path": str(prepared.notes_path),
        "config_path": config_path,
        "model": model or "",
        "verification": verification,
        "candidate_present": candidate_present,
        "verifier_payload": verifier_payload,
        "completion_summary": execution.completion_summary,
        "reward_summary": execution.reward_summary,
        "metadata": execution.metadata,
        "verifier_error": verifier_error,
        "status": status,
    }
    prepared.diagnostic_path.parent.mkdir(parents=True, exist_ok=True)
    prepared.diagnostic_path.write_text(json.dumps(diagnostic_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    raw_copy = raw_dir / f"{prepared.task_id}.json"
    raw_copy.parent.mkdir(parents=True, exist_ok=True)
    raw_copy.write_text(json.dumps(diagnostic_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    row: dict[str, Any] = {
        "task_id": prepared.task_id,
        "toolchain_id": _manifest_toolchain_id(manifest),
        "input_hash": prepared.input_hash,
        "prover_system": str(system_id).strip(),
        "budget_class": _manifest_budget_class(manifest),
        "status": status,
        "verification_log_digest": _canonical_digest(
            {
                "session_id": execution.session_id,
                "session_status": execution.session_status,
                "verification": verification,
                "verifier_error": verifier_error,
            }
        ),
        "run_id": str(manifest.get("run_id") or "").strip() or "cross-system-run",
        "attempts": 1,
        "repair_rounds_used": 0,
        "wall_clock_ms": execution.wall_clock_ms,
    }
    if verification.get("is_valid_no_sorry"):
        proof_dir.mkdir(parents=True, exist_ok=True)
        proof_out_path = proof_dir / f"{prepared.task_id}.lean"
        proof_out_path.write_text(proof_text, encoding="utf-8")
        row["proof_artifact_ref"] = str(proof_out_path)
    if verifier_error:
        row["error"] = verifier_error
    elif verification.get("error"):
        row["error"] = str(verification.get("error"))
    return row


def run_bb_slice(
    *,
    manifest_path: Path,
    task_inputs_path: Path,
    out_path: Path,
    summary_path: Path,
    system_id: str,
    config_path: str | None,
    model: str | None,
    proof_output_dir: str | None,
    raw_output_dir: str | None,
    workspace_root: str | None,
    base_url: str,
    start_engine: bool,
    engine_host: str,
    engine_port: int,
    engine_log_level: str,
    engine_wait_timeout_s: float,
    verifier_url: str,
    verifier_timeout_s: int,
    permission_mode: str,
    task_timeout_s: int | None,
    poll_interval_s: float,
    limit: int | None,
    task_runner: Callable[..., TaskExecutionResult] | None = None,
    verifier: Callable[..., dict[str, Any]] | None = None,
) -> dict[str, Any]:
    manifest = load_manifest(manifest_path)
    tasks = _load_task_inputs(task_inputs_path)
    if limit is not None:
        tasks = tasks[: max(0, int(limit))]
    if not tasks:
        raise ValueError("no tasks to run after applying limit")

    manifest_config = _manifest_system_config_ref(manifest, system_id=system_id)
    effective_config_path = _resolve_config_path(config_path or manifest_config)
    proof_dir, raw_dir, effective_workspace_root = _resolve_artifact_dirs(
        manifest,
        system_id=system_id,
        proof_output_dir=proof_output_dir,
        raw_output_dir=raw_output_dir,
        workspace_root=workspace_root,
    )
    proof_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    effective_workspace_root.mkdir(parents=True, exist_ok=True)

    engine_proc = _ensure_engine(
        base_url=base_url,
        start_engine=start_engine,
        repo_root=REPO_ROOT,
        engine_host=engine_host,
        engine_port=engine_port,
        engine_log_level=engine_log_level,
        wait_timeout_s=engine_wait_timeout_s,
    )
    client = BreadboardClient(base_url=base_url, timeout_s=max(10, int(task_timeout_s or 300) + 60))
    effective_task_timeout_s = max(
        30,
        int(task_timeout_s if task_timeout_s is not None else _manifest_wall_clock_cap_s(manifest) + 120),
    )

    try:
        rows = [
            _run_single_task(
                manifest=manifest,
                task=task,
                client=client,
                config_path=str(effective_config_path),
                model=model,
                system_id=system_id,
                proof_dir=proof_dir,
                raw_dir=raw_dir,
                workspace_root=effective_workspace_root,
                verifier_url=verifier_url,
                verifier_timeout_s=verifier_timeout_s,
                permission_mode=permission_mode,
                task_timeout_s=effective_task_timeout_s,
                poll_interval_s=poll_interval_s,
                task_runner=task_runner,
                verifier=verifier,
            )
            for task in tasks
        ]
    finally:
        if engine_proc is not None:
            with contextlib.suppress(Exception):
                engine_proc.terminate()
            with contextlib.suppress(Exception):
                engine_proc.wait(timeout=5.0)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(json.dumps(row, sort_keys=True) for row in rows) + "\n", encoding="utf-8")
    status_counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status") or "UNKNOWN")
        status_counts[status] = int(status_counts.get(status, 0)) + 1

    payload = {
        "schema": "breadboard.bb_atp_adapter_slice_run.v2",
        "ok": True,
        "manifest_path": str(manifest_path),
        "task_inputs_path": str(task_inputs_path),
        "result_path": str(out_path),
        "summary_generated_at": int(time.time()),
        "run_id": str(manifest.get("run_id") or "").strip() or "cross-system-run",
        "prover_system": str(system_id).strip(),
        "task_count": len(rows),
        "status_counts": status_counts,
        "config_ref": str(effective_config_path),
        "model": model or "",
        "proof_output_dir": str(proof_dir),
        "raw_output_dir": str(raw_dir),
        "workspace_root": str(effective_workspace_root),
        "verifier_url": verifier_url,
        "base_url": base_url,
    }
    dump_json(summary_path, payload)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--task-inputs", required=True)
    parser.add_argument(
        "--out",
        default="artifacts/benchmarks/bb_atp_adapter_normalized_results_v1.latest.jsonl",
    )
    parser.add_argument(
        "--summary-out",
        default="artifacts/benchmarks/bb_atp_adapter_slice_summary_v1.latest.json",
    )
    parser.add_argument("--system-id", default="bb_atp")
    parser.add_argument("--config", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--proof-output-dir", default="")
    parser.add_argument("--raw-output-dir", default="")
    parser.add_argument("--workspace-root", default="")
    parser.add_argument("--base-url", default=_engine_base_url(host="127.0.0.1", port=9099))
    parser.add_argument("--start-engine", action="store_true")
    parser.add_argument("--engine-host", default="127.0.0.1")
    parser.add_argument("--engine-port", type=int, default=9099)
    parser.add_argument("--engine-log-level", default="warning")
    parser.add_argument("--engine-wait-timeout-s", type=float, default=20.0)
    parser.add_argument("--verifier-url", default="http://127.0.0.1:18001/verify")
    parser.add_argument("--verifier-timeout-s", type=int, default=180)
    parser.add_argument("--permission-mode", default="bypass")
    parser.add_argument("--task-timeout-s", type=int, default=0)
    parser.add_argument("--poll-interval-s", type=float, default=3.0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    summary = run_bb_slice(
        manifest_path=Path(args.manifest).resolve(),
        task_inputs_path=Path(args.task_inputs).resolve(),
        out_path=Path(args.out).resolve(),
        summary_path=Path(args.summary_out).resolve(),
        system_id=str(args.system_id).strip(),
        config_path=str(args.config).strip() or None,
        model=str(args.model).strip() or None,
        proof_output_dir=str(args.proof_output_dir).strip() or None,
        raw_output_dir=str(args.raw_output_dir).strip() or None,
        workspace_root=str(args.workspace_root).strip() or None,
        base_url=str(args.base_url).strip(),
        start_engine=bool(args.start_engine),
        engine_host=str(args.engine_host).strip(),
        engine_port=int(args.engine_port),
        engine_log_level=str(args.engine_log_level).strip(),
        engine_wait_timeout_s=float(args.engine_wait_timeout_s),
        verifier_url=str(args.verifier_url).strip(),
        verifier_timeout_s=max(10, int(args.verifier_timeout_s)),
        permission_mode=str(args.permission_mode).strip() or "bypass",
        task_timeout_s=(int(args.task_timeout_s) if int(args.task_timeout_s) > 0 else None),
        poll_interval_s=max(0.1, float(args.poll_interval_s)),
        limit=(int(args.limit) if int(args.limit) > 0 else None),
    )
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print(
            "[bb-atp-adapter-slice-v1] "
            f"ok={summary['ok']} tasks={summary['task_count']} "
            f"result_path={summary['result_path']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
