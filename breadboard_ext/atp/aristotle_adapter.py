from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Protocol


DEFAULT_BASE_URL = "https://aristotle.harmonic.fun/api/v1"
_POLL_RUNNING_STATUSES = {"NOT_STARTED", "QUEUED", "IN_PROGRESS"}
_CAPACITY_BUSY_STATUSES = {"QUEUED", "IN_PROGRESS"}
_TERMINAL_STATUSES = {"COMPLETE", "FAILED", "CANCELED"}
_TRANSIENT_ERROR_MARKERS = (
    "status 429",
    "too many requests",
    "timed out",
    "timeout",
    "temporarily unavailable",
    "connection",
    "network",
)
_CAPACITY_ERROR_MARKERS = (
    "already have 5 projects in progress",
    "projects in progress",
)


class _AsyncRequestClient(Protocol):
    async def __aenter__(self) -> "_AsyncRequestClient": ...
    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None: ...
    async def get(self, endpoint: str, params: dict[str, Any] | None = None) -> Any: ...
    async def post(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
        files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
    ) -> Any: ...


def _load_repo_env(path: str = ".env") -> None:
    if not os.path.exists(path):
        return
    for raw_line in Path(path).read_text(encoding="utf-8").splitlines():
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


def _sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _canonical_digest(payload: dict[str, Any]) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return _sha256_hex(text.encode("utf-8"))


def normalize_input_hash(input_text: str) -> str:
    normalized = " ".join(str(input_text).split())
    return _sha256_hex(normalized.encode("utf-8"))


def build_toolchain_id(lean_version: str, mathlib_commit: str) -> str:
    version = str(lean_version).strip()
    commit = str(mathlib_commit).strip()
    return f"lean{version}_mathlib.{commit}"


def _is_transient_error(exc: Exception) -> bool:
    lowered = str(exc).strip().lower()
    return any(marker in lowered for marker in _TRANSIENT_ERROR_MARKERS)


def _is_capacity_error(exc: Exception) -> bool:
    lowered = str(exc).strip().lower()
    return any(marker in lowered for marker in _CAPACITY_ERROR_MARKERS)


def _project_type_for_mode(input_mode: str) -> int:
    mode = str(input_mode or "").strip().lower()
    if mode == "formal_lean":
        return 2
    if mode == "informal":
        return 3
    raise ValueError(f"unsupported input_mode={input_mode!r} (expected formal_lean|informal)")


def _map_status(
    *,
    project_status: str,
    timed_out: bool,
    error_text: str | None,
    solution_text: str | None,
) -> str:
    if timed_out:
        return "TIMEOUT"
    if error_text:
        return "ERROR"
    if project_status == "COMPLETE":
        output = (solution_text or "").strip()
        if not output:
            return "UNSOLVED"
        if "sorry" in output.lower():
            return "UNSOLVED"
        return "SOLVED"
    if project_status in {"FAILED", "CANCELED"}:
        return "ERROR"
    return "UNSOLVED"


def _decode_response_json(response: Any) -> dict[str, Any]:
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("API response was not a JSON object")
    return payload


def _to_bytes(response: Any) -> bytes:
    content = getattr(response, "content", None)
    if isinstance(content, bytes):
        return content
    if content is None:
        return b""
    if isinstance(content, str):
        return content.encode("utf-8")
    return bytes(content)


def _parse_iso_utc(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(frozen=True)
class AristotleTaskInput:
    task_id: str
    input_text: str
    input_mode: str = "informal"
    input_hash: str | None = None
    formal_input_context: str | None = None


@dataclass(frozen=True)
class AristotleRunConfig:
    run_id: str
    prover_system: str
    toolchain_id: str
    budget_class: str
    wall_clock_cap_s: int
    poll_interval_s: float = 15.0


class AristotleProjectAdapter:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        request_client_cls: type[_AsyncRequestClient] | None = None,
        sleep_fn: Callable[[float], Any] | None = None,
        time_monotonic_fn: Callable[[], float] | None = None,
    ) -> None:
        _load_repo_env(".env")
        resolved_api_key = str(api_key or os.environ.get("ARISTOTLE_API_KEY") or "").strip()
        if not resolved_api_key:
            raise RuntimeError("ARISTOTLE_API_KEY is required")
        self.api_key = resolved_api_key
        self.base_url = str(base_url or os.environ.get("ARISTOTLE_BASE_URL") or DEFAULT_BASE_URL).strip()
        self._sleep = sleep_fn or asyncio.sleep
        self._time_monotonic = time_monotonic_fn or time.monotonic

        if request_client_cls is not None:
            self._request_client_cls = request_client_cls
        else:
            self._request_client_cls = self._resolve_default_client()

    def _resolve_default_client(self) -> type[_AsyncRequestClient]:
        try:
            import aristotlelib.api_request as api_request  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "aristotlelib is required. Install with: uv pip install aristotlelib (or pip install aristotlelib)"
            ) from exc
        api_request.BASE_URL = self.base_url
        if hasattr(api_request, "set_api_key"):
            api_request.set_api_key(self.api_key)
        return api_request.AristotleRequestClient

    async def _request_with_retry(
        self,
        *,
        method: str,
        endpoint: str,
        params: dict[str, Any] | None = None,
        files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
    ) -> Any:
        max_attempts = max(1, int(self._max_retries()))
        capacity_retry_attempts = max(0, int(self._capacity_retry_attempts()))
        delay_seconds = 1.0
        last_exc: Exception | None = None
        capacity_attempts = 0
        for attempt in range(1, max_attempts + 1):
            try:
                async with self._request_client_cls() as client:
                    if method == "GET":
                        return await client.get(endpoint, params=params)
                    if method == "POST":
                        return await client.post(endpoint, params=params, files=files)
                    raise ValueError(f"unsupported method={method!r}")
            except Exception as exc:
                last_exc = exc
                if _is_capacity_error(exc) and capacity_attempts < capacity_retry_attempts:
                    capacity_attempts += 1
                    await self._sleep(self._capacity_retry_sleep_seconds())
                    continue
                if attempt >= max_attempts or (not _is_transient_error(exc)):
                    raise
                await self._sleep(delay_seconds)
                delay_seconds = min(delay_seconds * 2.0, 10.0)
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("request failed unexpectedly")

    def _max_retries(self) -> int:
        value = int(os.environ.get("ARISTOTLE_TRANSIENT_RETRIES", "0") or "0")
        if value > 0:
            return value
        return 3

    def _capacity_retry_attempts(self) -> int:
        value = int(os.environ.get("ARISTOTLE_CAPACITY_RETRY_ATTEMPTS", "0") or "0")
        if value > 0:
            return value
        return 40

    def _capacity_retry_sleep_seconds(self) -> float:
        value = float(os.environ.get("ARISTOTLE_CAPACITY_RETRY_SLEEP_SECONDS", "0") or "0")
        if value > 0:
            return value
        return 20.0

    def _capacity_wait_enabled(self) -> bool:
        raw = str(os.environ.get("ARISTOTLE_ENABLE_CAPACITY_WAIT", "1")).strip().lower()
        return raw not in {"0", "false", "no", "off"}

    def _max_in_progress(self) -> int:
        value = int(os.environ.get("ARISTOTLE_MAX_IN_PROGRESS", "0") or "0")
        if value > 0:
            return value
        return 4

    def _capacity_wait_poll_seconds(self) -> float:
        value = float(os.environ.get("ARISTOTLE_CAPACITY_WAIT_POLL_SECONDS", "0") or "0")
        if value > 0:
            return value
        return 15.0

    def _capacity_wait_max_seconds(self) -> int:
        value = int(os.environ.get("ARISTOTLE_CAPACITY_WAIT_MAX_SECONDS", "0") or "0")
        if value > 0:
            return value
        return 1800

    def _capacity_wait_strict(self) -> bool:
        raw = str(os.environ.get("ARISTOTLE_CAPACITY_WAIT_STRICT", "0")).strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def _capacity_count_not_started(self) -> bool:
        raw = str(os.environ.get("ARISTOTLE_CAPACITY_COUNT_NOT_STARTED", "0")).strip().lower()
        return raw in {"1", "true", "yes", "on"}

    def _capacity_not_started_max_age_seconds(self) -> int:
        value = int(os.environ.get("ARISTOTLE_CAPACITY_NOT_STARTED_MAX_AGE_SECONDS", "0") or "0")
        if value > 0:
            return value
        return 120

    def _timeout_cleanup_enabled(self) -> bool:
        raw = str(os.environ.get("ARISTOTLE_TIMEOUT_CLEANUP_ENABLED", "1")).strip().lower()
        return raw not in {"0", "false", "no", "off"}

    def _timeout_cleanup_wait_seconds(self) -> int:
        value = int(os.environ.get("ARISTOTLE_TIMEOUT_CLEANUP_WAIT_SECONDS", "0") or "0")
        if value > 0:
            return value
        return 60

    def _project_is_capacity_busy(self, row: dict[str, Any]) -> bool:
        status = str(row.get("status") or "").strip().upper()
        if status in _CAPACITY_BUSY_STATUSES:
            return True
        if status != "NOT_STARTED" or not self._capacity_count_not_started():
            return False
        max_age = max(1, int(self._capacity_not_started_max_age_seconds()))
        reference = _parse_iso_utc(row.get("last_updated_at")) or _parse_iso_utc(row.get("updated_at")) or _parse_iso_utc(
            row.get("created_at")
        )
        if reference is None:
            return False
        age_seconds = (datetime.now(timezone.utc) - reference).total_seconds()
        return age_seconds <= float(max_age)

    async def _list_projects(self, *, limit: int = 100) -> list[dict[str, Any]]:
        response = await self._request_with_retry(method="GET", endpoint="/project", params={"limit": int(limit)})
        payload = _decode_response_json(response)
        projects = payload.get("projects") if isinstance(payload, dict) else None
        if not isinstance(projects, list):
            return []
        return [row for row in projects if isinstance(row, dict)]

    async def _wait_for_capacity(self) -> None:
        if not self._capacity_wait_enabled():
            return
        max_in_progress = max(1, int(self._max_in_progress()))
        started = self._time_monotonic()
        while True:
            try:
                projects = await self._list_projects(limit=max(50, max_in_progress * 10))
                in_progress = sum(
                    1
                    for row in projects
                    if self._project_is_capacity_busy(row)
                )
            except Exception:
                return
            if in_progress < max_in_progress:
                return
            waited = self._time_monotonic() - started
            if waited >= float(self._capacity_wait_max_seconds()):
                if self._capacity_wait_strict():
                    raise RuntimeError(
                        f"capacity wait timeout; in_progress={in_progress} max_allowed={max_in_progress - 1}"
                    )
                return
            await self._sleep(max(0.1, float(self._capacity_wait_poll_seconds())))

    async def _create_project(self, *, input_mode: str) -> dict[str, Any]:
        project_type = _project_type_for_mode(input_mode)
        response = await self._request_with_retry(method="POST", endpoint=f"/project?project_type={project_type}")
        return _decode_response_json(response)

    async def _start_solve(
        self,
        *,
        project_id: str,
        input_text: str,
        formal_input_context: str | None,
    ) -> dict[str, Any]:
        files = None
        if formal_input_context:
            context_path = Path(formal_input_context).resolve()
            content = context_path.read_bytes()
            files = [("formal_input_context", (str(context_path), content, "text/plain"))]
        response = await self._request_with_retry(
            method="POST",
            endpoint=f"/project/{project_id}/solve",
            params={"input_text": input_text},
            files=files,
        )
        return _decode_response_json(response)

    async def _poll_project(self, *, project_id: str) -> dict[str, Any]:
        response = await self._request_with_retry(method="GET", endpoint=f"/project/{project_id}")
        return _decode_response_json(response)

    async def _fetch_result(self, *, project_id: str) -> bytes:
        response = await self._request_with_retry(method="GET", endpoint=f"/project/{project_id}/result")
        return _to_bytes(response)

    async def _cancel_project(self, *, project_id: str) -> dict[str, Any]:
        response = await self._request_with_retry(method="POST", endpoint=f"/project/{project_id}/cancel")
        return _decode_response_json(response)

    async def _cleanup_timed_out_project(self, *, project_id: str, run: AristotleRunConfig) -> tuple[str, str | None]:
        if not project_id or not self._timeout_cleanup_enabled():
            return "TIMEOUT", None

        deadline = self._time_monotonic() + max(1, int(self._timeout_cleanup_wait_seconds()))
        cancel_attempted = False
        last_status = "TIMEOUT"
        last_error: str | None = None

        while True:
            try:
                polled = await self._poll_project(project_id=project_id)
                last_status = str(polled.get("status") or "").strip().upper() or last_status
                if last_status in _TERMINAL_STATUSES:
                    return last_status, last_error
            except Exception as exc:
                last_error = str(exc)

            if not cancel_attempted:
                cancel_attempted = True
                try:
                    canceled = await self._cancel_project(project_id=project_id)
                    last_status = str(canceled.get("status") or "").strip().upper() or last_status
                    if last_status in _TERMINAL_STATUSES:
                        return last_status, last_error
                except Exception as exc:
                    last_error = str(exc)

            if self._time_monotonic() >= deadline:
                return last_status, last_error
            await self._sleep(min(max(0.1, float(run.poll_interval_s)), 5.0))

    async def run_task(
        self,
        *,
        task: AristotleTaskInput,
        run: AristotleRunConfig,
        proof_output_dir: Path | None = None,
    ) -> dict[str, Any]:
        start = self._time_monotonic()
        project_id = ""
        project_status = "UNKNOWN"
        error_text: str | None = None
        timed_out = False
        solution_bytes = b""
        proof_artifact_ref: str | None = None

        try:
            await self._wait_for_capacity()
            created = await self._create_project(input_mode=task.input_mode)
            project_id = str(created.get("project_id") or "").strip()
            if not project_id:
                raise RuntimeError("create project response missing project_id")

            solving = await self._start_solve(
                project_id=project_id,
                input_text=task.input_text,
                formal_input_context=task.formal_input_context,
            )
            project_status = str(solving.get("status") or "").strip().upper() or project_status

            deadline = start + max(1, int(run.wall_clock_cap_s))
            while True:
                if self._time_monotonic() >= deadline:
                    timed_out = True
                    project_status = "TIMEOUT"
                    break
                polled = await self._poll_project(project_id=project_id)
                project_status = str(polled.get("status") or "").strip().upper() or project_status
                if project_status in _TERMINAL_STATUSES:
                    break
                if project_status not in _POLL_RUNNING_STATUSES:
                    break
                await self._sleep(max(0.1, float(run.poll_interval_s)))

            if timed_out and project_id:
                project_status, cleanup_error = await self._cleanup_timed_out_project(project_id=project_id, run=run)
                if cleanup_error:
                    error_text = cleanup_error

            if not timed_out and project_status == "COMPLETE":
                solution_bytes = await self._fetch_result(project_id=project_id)
                if proof_output_dir is not None:
                    proof_output_dir.mkdir(parents=True, exist_ok=True)
                    proof_path = proof_output_dir / f"{task.task_id}.lean"
                    proof_path.write_bytes(solution_bytes)
                    proof_artifact_ref = str(proof_path)
        except Exception as exc:
            error_text = str(exc)

        wall_clock_ms = max(0, int((self._time_monotonic() - start) * 1000.0))
        solution_text = solution_bytes.decode("utf-8", errors="replace") if solution_bytes else None
        status = _map_status(
            project_status=project_status,
            timed_out=timed_out,
            error_text=error_text,
            solution_text=solution_text,
        )
        verification_payload = {
            "project_id": project_id,
            "project_status": project_status,
            "timed_out": timed_out,
            "solution_sha256": _sha256_hex(solution_bytes) if solution_bytes else "",
            "error": error_text or "",
        }
        row: dict[str, Any] = {
            "task_id": str(task.task_id).strip(),
            "toolchain_id": str(run.toolchain_id).strip(),
            "input_hash": str(task.input_hash or normalize_input_hash(task.input_text)).strip(),
            "prover_system": str(run.prover_system).strip(),
            "budget_class": str(run.budget_class).strip(),
            "status": status,
            "verification_log_digest": _canonical_digest(verification_payload),
            "run_id": str(run.run_id).strip(),
            "attempts": 1,
            "repair_rounds_used": 0,
            "wall_clock_ms": wall_clock_ms,
        }
        if proof_artifact_ref:
            row["proof_artifact_ref"] = proof_artifact_ref
        if error_text:
            row["error"] = error_text
        return row

    async def run_tasks(
        self,
        *,
        tasks: Iterable[AristotleTaskInput],
        run: AristotleRunConfig,
        max_concurrency: int = 1,
        proof_output_dir: Path | None = None,
        fail_fast: bool = False,
    ) -> list[dict[str, Any]]:
        entries = list(tasks)
        if not entries:
            return []
        semaphore = asyncio.Semaphore(max(1, int(max_concurrency)))
        outputs: list[tuple[int, dict[str, Any]]] = []

        async def _run_single(index: int, task: AristotleTaskInput) -> None:
            async with semaphore:
                row = await self.run_task(task=task, run=run, proof_output_dir=proof_output_dir)
                outputs.append((index, row))
                if fail_fast and row.get("status") in {"ERROR", "TIMEOUT"}:
                    raise RuntimeError(f"fail_fast stop: task_id={task.task_id} status={row.get('status')}")

        await asyncio.gather(*[_run_single(index, task) for index, task in enumerate(entries)])
        outputs.sort(key=lambda item: item[0])
        return [row for _, row in outputs]
