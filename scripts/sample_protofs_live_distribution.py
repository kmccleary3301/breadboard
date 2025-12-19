#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _load_env_file(path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and value:
            env[key] = value
    return env


def _ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")


def _run(
    cmd: List[str],
    *,
    cwd: Path,
    env: Dict[str, str],
    timeout_s: int,
    stdout_path: Path,
    stderr_path: Path,
) -> Tuple[subprocess.CompletedProcess, bool]:
    with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
        try:
            proc = subprocess.run(
                cmd,
                cwd=str(cwd),
                env=env,
                stdout=out,
                stderr=err,
                timeout=timeout_s,
                check=False,
            )
            return proc, False
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(cmd, returncode=124), True


def _select_compile_sources(workspace: Path) -> List[str]:
    sources = sorted(p.name for p in workspace.glob("*.c"))
    if not sources:
        return []

    main_sources: List[str] = []
    main_re = re.compile(r"\bint\s+main\s*\(")
    for name in sources:
        try:
            text = (workspace / name).read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        if main_re.search(text):
            main_sources.append(name)

    if len(main_sources) <= 1:
        return sources

    preferred = None
    if "test.c" in main_sources:
        preferred = "test.c"
    else:
        testish = sorted([name for name in main_sources if name.startswith("test")])
        if testish:
            preferred = testish[0]
        else:
            preferred = sorted(main_sources)[0]

    selected = [name for name in sources if name not in main_sources]
    selected.append(preferred)
    return selected


def _eval_workspace(workspace: Path, *, out_dir: Path) -> Dict[str, Any]:
    compiled_sources = _select_compile_sources(workspace)
    if compiled_sources:
        joined = " ".join(compiled_sources)
        test_cmd = ["bash", "-lc", f"gcc -Wall -Wextra -std=c11 -O0 -g -o test {joined} && ./test"]
    else:
        test_cmd = ["bash", "-lc", "echo 'no C sources found' && exit 2"]

    clean_cmd: List[str] = ["make", "clean"] if (workspace / "Makefile").exists() else []
    make_result: Optional[Dict[str, Any]] = None
    if (workspace / "Makefile").exists():
        make_proc = subprocess.run(
            ["make", "clean", "test"],
            cwd=str(workspace),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        (out_dir / "make.stdout.txt").write_bytes(make_proc.stdout)
        (out_dir / "make.stderr.txt").write_bytes(make_proc.stderr)
        make_result = {
            "exit_code": make_proc.returncode,
            "stdout_tail": make_proc.stdout.decode("utf-8", errors="replace")[-2000:],
            "stderr_tail": make_proc.stderr.decode("utf-8", errors="replace")[-2000:],
        }
    result: Dict[str, Any] = {
        "workspace": str(workspace),
        "clean_cmd": clean_cmd,
        "test_cmd": test_cmd,
        "compiled_sources": compiled_sources,
        "make": make_result,
        "clean_exit_code": None,
        "test_exit_code": None,
        "test_stdout": None,
        "test_stderr": None,
    }

    if clean_cmd:
        clean_proc = subprocess.run(clean_cmd, cwd=str(workspace), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
        result["clean_exit_code"] = clean_proc.returncode
        (out_dir / "build_clean.stdout.txt").write_bytes(clean_proc.stdout)
        (out_dir / "build_clean.stderr.txt").write_bytes(clean_proc.stderr)

    test_proc = subprocess.run(test_cmd, cwd=str(workspace), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    result["test_exit_code"] = test_proc.returncode
    (out_dir / "build_test.stdout.txt").write_bytes(test_proc.stdout)
    (out_dir / "build_test.stderr.txt").write_bytes(test_proc.stderr)
    result["test_stdout"] = test_proc.stdout.decode("utf-8", errors="replace")[-4000:]
    result["test_stderr"] = test_proc.stderr.decode("utf-8", errors="replace")[-4000:]
    result["passed"] = test_proc.returncode == 0
    result["files_present"] = sorted(
        str(p.relative_to(workspace).as_posix())
        for p in workspace.rglob("*")
        if p.is_file() and not any(part in {".git", ".kyle", ".cache", "__pycache__"} for part in p.parts)
    )
    return result


def _build_manifest(workspace: Path) -> Dict[str, Any]:
    from agentic_coder_prototype.logging_v2.workspace_manifest import build_workspace_manifest

    return build_workspace_manifest(workspace)


def _parse_opencode_json_events(path: Path) -> Dict[str, Any]:
    session_id: Optional[str] = None
    tool_counts: Dict[str, int] = {}
    tool_sequence: List[str] = []
    error_events: List[Dict[str, Any]] = []

    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except Exception:
            continue
        if not isinstance(event, dict):
            continue
        sid = event.get("sessionID")
        if isinstance(sid, str) and sid:
            session_id = session_id or sid
        etype = event.get("type")
        if etype == "tool_use":
            part = event.get("part") or {}
            tool = None
            if isinstance(part, dict):
                tool = part.get("tool")
            if isinstance(tool, str) and tool:
                tool_counts[tool] = tool_counts.get(tool, 0) + 1
                tool_sequence.append(tool)
        if etype in {"error", "session.error"}:
            error_events.append(event)

    return {
        "session_id": session_id,
        "tool_counts": tool_counts,
        "tool_sequence": tool_sequence,
        "error_events": error_events,
    }


def _run_opencode_sample(
    *,
    index: int,
    out_root: Path,
    workspace: Path,
    task_path: Path,
    model: str,
    env: Dict[str, str],
    timeout_s: int,
) -> Dict[str, Any]:
    sample_dir = out_root / f"opencode_{index:02d}"
    _ensure_dir(sample_dir)
    _ensure_dir(workspace)

    stdout_path = sample_dir / "opencode.stdout.jsonl"
    stderr_path = sample_dir / "opencode.stderr.txt"

    task_text = task_path.read_text(encoding="utf-8", errors="replace").strip()
    message = (
        "Complete the following task in the current directory.\n\n"
        f"{task_text}\n\n"
        "Constraints:\n"
        "- Avoid repeated listing; do at most one broad list at the start.\n"
        "- Prefer targeted reads/edits.\n\n"
        "Create whatever files you need (fs.c/fs.h or protofilesystem.c/protofilesystem.h, test.c, Makefile). "
        "Run the build/tests with gcc/make until they pass. Stop when complete."
    )

    cmd = [
        "opencode",
        "run",
        "--format",
        "json",
        "--model",
        model,
        "--agent",
        "build",
        "--title",
        f"protofs_sample_opencode_{index:02d}",
        message,
    ]

    proc, timed_out = _run(
        cmd,
        cwd=workspace,
        env=env,
        timeout_s=timeout_s,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )

    parsed = _parse_opencode_json_events(stdout_path)
    export_path = None
    if parsed.get("session_id") and not timed_out:
        export_path = sample_dir / "opencode_export.json"
        export_proc = subprocess.run(
            ["opencode", "export", str(parsed["session_id"])],
            cwd=str(workspace),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=120,
        )
        export_path.write_bytes(export_proc.stdout)
        (sample_dir / "opencode_export.stderr.txt").write_bytes(export_proc.stderr)

    eval_dir = sample_dir / "eval"
    _ensure_dir(eval_dir)
    eval_result = _eval_workspace(workspace, out_dir=eval_dir)

    manifest = _build_manifest(workspace)
    _write_json(sample_dir / "workspace.manifest.json", manifest)

    payload = {
        "system": "opencode",
        "index": index,
        "workspace": str(workspace),
        "model": model,
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "session_id": parsed.get("session_id"),
        "tool_counts": parsed.get("tool_counts"),
        "tool_sequence": parsed.get("tool_sequence")[:2000],
        "errors": parsed.get("error_events"),
        "export_path": str(export_path) if export_path else None,
        "eval": eval_result,
    }
    _write_json(sample_dir / "sample_summary.json", payload)
    return payload


def _run_kylecode_sample(
    *,
    index: int,
    out_root: Path,
    workspace: Path,
    task_path: Path,
    config_path: Path,
    max_iterations: int,
    env: Dict[str, str],
    timeout_s: int,
) -> Dict[str, Any]:
    sample_dir = out_root / f"kylecode_{index:02d}"
    _ensure_dir(sample_dir)
    _ensure_dir(workspace)

    stdout_path = sample_dir / "kylecode.stdout.txt"
    stderr_path = sample_dir / "kylecode.stderr.txt"
    result_json_path = sample_dir / "kylecode.result.json"

    cmd = [
        sys.executable,
        str(REPO_ROOT / "main.py"),
        str(config_path),
        "--task",
        str(task_path),
        "--workspace",
        str(workspace),
        "--max-iterations",
        str(max_iterations),
        "--result-json",
        str(result_json_path),
    ]

    proc, timed_out = _run(
        cmd,
        cwd=REPO_ROOT,
        env=env,
        timeout_s=timeout_s,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )

    result_payload: Dict[str, Any] = {}
    if result_json_path.exists():
        try:
            result_payload = json.loads(result_json_path.read_text(encoding="utf-8"))
        except Exception:
            result_payload = {}

    run_dir = result_payload.get("run_dir") or result_payload.get("logging_dir")
    run_summary = None
    tool_usage = None
    completion = None
    if isinstance(run_dir, str) and run_dir:
        summary_path = Path(run_dir) / "meta" / "run_summary.json"
        if summary_path.exists():
            try:
                run_summary = json.loads(summary_path.read_text(encoding="utf-8"))
                tool_usage = run_summary.get("tool_usage")
                completion = run_summary.get("completion_summary")
            except Exception:
                run_summary = None

    eval_dir = sample_dir / "eval"
    _ensure_dir(eval_dir)
    eval_result = _eval_workspace(workspace, out_dir=eval_dir)

    manifest = _build_manifest(workspace)
    _write_json(sample_dir / "workspace.manifest.json", manifest)

    payload = {
        "system": "kylecode",
        "index": index,
        "workspace": str(workspace),
        "config": str(config_path),
        "exit_code": proc.returncode,
        "timed_out": timed_out,
        "run_dir": run_dir,
        "completion_summary": completion,
        "tool_usage": tool_usage,
        "eval": eval_result,
    }
    _write_json(sample_dir / "sample_summary.json", payload)
    return payload


def _summarize(samples: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(samples)
    passed = sum(1 for s in samples if s.get("eval", {}).get("passed"))
    exit_ok = sum(1 for s in samples if s.get("exit_code") == 0)

    tool_totals: Dict[str, int] = {}
    file_presence: Dict[str, int] = {}
    for sample in samples:
        tool_counts = sample.get("tool_counts") or sample.get("tool_usage") or {}
        if isinstance(tool_counts, dict):
            for k, v in tool_counts.items():
                if isinstance(v, int):
                    tool_totals[k] = tool_totals.get(k, 0) + v
                elif isinstance(v, dict) and "count" in v and isinstance(v["count"], int):
                    tool_totals[k] = tool_totals.get(k, 0) + v["count"]
        files = sample.get("eval", {}).get("files_present") or []
        if isinstance(files, list):
            for fname in files:
                if isinstance(fname, str):
                    file_presence[fname] = file_presence.get(fname, 0) + 1

    return {
        "total_runs": total,
        "exit_code_zero": exit_ok,
        "tests_passed": passed,
        "tests_failed": total - passed,
        "tool_totals": dict(sorted(tool_totals.items(), key=lambda kv: (-kv[1], kv[0]))),
        "file_presence_top": dict(sorted(file_presence.items(), key=lambda kv: (-kv[1], kv[0]))[:30]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Sample protofs live runs for OpenCode vs KyleCode.")
    parser.add_argument("--runs", type=int, default=5, help="Number of samples per system (default: 5)")
    parser.add_argument("--model", default="openai/gpt-5.1-codex-mini", help="OpenCode model id (provider/model)")
    parser.add_argument("--task", default="implementations/test_tasks/protofs_c.md", help="Task markdown path")
    parser.add_argument(
        "--kc-config",
        default="agent_configs/opencode_openai_codexmini_c_fs_cli_shared.yaml",
        help="KyleCode config path",
    )
    parser.add_argument("--kc-max-iterations", type=int, default=40, help="KyleCode max iterations")
    parser.add_argument("--timeout-s", type=int, default=1800, help="Per-run timeout in seconds (default: 1800)")
    parser.add_argument("--out-dir", default=None, help="Output directory (default: artifacts/protofs_samples/<ts>)")
    parser.add_argument("--skip-opencode", action="store_true", help="Skip OpenCode sampling")
    parser.add_argument("--skip-kylecode", action="store_true", help="Skip KyleCode sampling")
    args = parser.parse_args()

    task_path = (REPO_ROOT / args.task).resolve() if not Path(args.task).is_absolute() else Path(args.task).resolve()
    if not task_path.exists():
        raise SystemExit(f"Task not found: {task_path}")
    kc_config_path = (REPO_ROOT / args.kc_config).resolve() if not Path(args.kc_config).is_absolute() else Path(args.kc_config).resolve()
    if not kc_config_path.exists():
        raise SystemExit(f"KyleCode config not found: {kc_config_path}")

    out_root = Path(args.out_dir) if args.out_dir else (REPO_ROOT / "artifacts" / "protofs_samples" / _now_stamp())
    _ensure_dir(out_root)

    base_env = os.environ.copy()
    env_from_file = _load_env_file(REPO_ROOT / ".env")
    for k, v in env_from_file.items():
        base_env.setdefault(k, v)

    base_env.setdefault("OPENCODE_DISABLE_LSP_DOWNLOAD", "1")
    base_env.setdefault("OPENCODE_DISABLE_PRUNE", "1")
    base_env.setdefault("OPENCODE_DISABLE_AUTOCOMPACT", "1")
    base_env.setdefault(
        "OPENCODE_PERMISSION",
        json.dumps(
            {
                "edit": "allow",
                "bash": "allow",
                "webfetch": "allow",
                "external_directory": "allow",
                "doom_loop": "allow",
            }
        ),
    )

    meta = {
        "generated_at": _dt.datetime.now().isoformat(),
        "task": str(task_path),
        "model": args.model,
        "runs": args.runs,
        "kc_config": str(kc_config_path),
        "kc_max_iterations": args.kc_max_iterations,
        "timeout_s": args.timeout_s,
    }
    _write_json(out_root / "experiment_meta.json", meta)

    samples_opencode: List[Dict[str, Any]] = []
    samples_kylecode: List[Dict[str, Any]] = []

    tmp_root = Path("/tmp") / f"protofs_samples_{_now_stamp()}"
    _ensure_dir(tmp_root)

    if not args.skip_opencode:
        for idx in range(1, args.runs + 1):
            ws = tmp_root / f"opencode_run_{idx:02d}"
            if ws.exists():
                shutil.rmtree(ws, ignore_errors=True)
            _ensure_dir(ws)
            sample = (
                _run_opencode_sample(
                    index=idx,
                    out_root=out_root,
                    workspace=ws,
                    task_path=task_path,
                    model=args.model,
                    env=base_env,
                    timeout_s=args.timeout_s,
                )
            )
            samples_opencode.append(sample)
            print(
                f"[opencode {idx:02d}/{args.runs}] exit={sample.get('exit_code')} "
                f"timeout={bool(sample.get('timed_out'))} tests_passed={bool(sample.get('eval', {}).get('passed'))}",
                flush=True,
            )

    if not args.skip_kylecode:
        for idx in range(1, args.runs + 1):
            ws = tmp_root / f"kylecode_run_{idx:02d}"
            if ws.exists():
                shutil.rmtree(ws, ignore_errors=True)
            _ensure_dir(ws)
            sample = (
                _run_kylecode_sample(
                    index=idx,
                    out_root=out_root,
                    workspace=ws,
                    task_path=task_path,
                    config_path=kc_config_path,
                    max_iterations=args.kc_max_iterations,
                    env=base_env,
                    timeout_s=args.timeout_s,
                )
            )
            samples_kylecode.append(sample)
            print(
                f"[kylecode {idx:02d}/{args.runs}] exit={sample.get('exit_code')} "
                f"timeout={bool(sample.get('timed_out'))} tests_passed={bool(sample.get('eval', {}).get('passed'))}",
                flush=True,
            )

    report = {
        "meta": meta,
        "opencode": {
            "samples": samples_opencode,
            "summary": _summarize(samples_opencode),
        },
        "kylecode": {
            "samples": samples_kylecode,
            "summary": _summarize(samples_kylecode),
        },
    }
    _write_json(out_root / "distribution_report.json", report)

    # Human-readable quick summary
    lines: List[str] = []
    lines.append(f"Output: {out_root}")
    if samples_opencode:
        s = report["opencode"]["summary"]
        lines.append(f"OpenCode: {s['tests_passed']}/{s['total_runs']} tests passed (exit0={s['exit_code_zero']})")
    if samples_kylecode:
        s = report["kylecode"]["summary"]
        lines.append(f"KyleCode: {s['tests_passed']}/{s['total_runs']} tests passed (exit0={s['exit_code_zero']})")
    (out_root / "SUMMARY.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

