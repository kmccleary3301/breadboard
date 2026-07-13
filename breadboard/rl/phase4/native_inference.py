from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Protocol

BREADBOARD_NATIVE_INFERENCE_OWNER = "breadboard_native_sub_inference_lane"
BREADBOARD_NATIVE_LANE_SCHEMA = "bb.rl.phase4.native_inference_lane.v1"


class CompletionSession(Protocol):
    def post(self, url: str, *, json: dict[str, Any], timeout: float): ...  # pragma: no cover - protocol


class CompletionResponseLike(Protocol):
    status_code: int

    def json(self) -> dict[str, Any]: ...  # pragma: no cover - protocol
    def raise_for_status(self) -> None: ...  # pragma: no cover - protocol


class TokenizerLike(Protocol):
    def decode(self, token_ids: list[int], *, skip_special_tokens: bool = False) -> str: ...
    def encode(self, text: str, *, add_special_tokens: bool = False) -> list[int]: ...


def sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def sha256_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def sha256_json(payload: Any) -> str:
    return sha256_text(json.dumps(payload, sort_keys=True, separators=(",", ":")))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def _safe_id_part(value: object) -> str:
    text = str(value or "").strip()
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-", "."} else "-" for ch in text).strip("-._")
    return safe[:96] or "request"


@dataclass(frozen=True)
class NativeCompletionResponse:
    request_id: str
    upstream_request_id: str
    response_id: str
    model_ref: str
    prompt_text: str
    output_text: str
    posthoc_token_ids: list[int]
    backend_token_texts: list[str]
    backend_token_logprobs: list[float]
    backend_token_ids: list[int]
    latency_ms: float
    http_status: int
    request_sha256: str
    response_sha256: str
    output_text_sha256: str
    posthoc_token_ids_sha256: str
    backend_token_texts_sha256: str
    backend_token_logprobs_sha256: str
    backend_token_ids_sha256: str
    backend_completion_id: str
    passed: bool
    error_type: str = ""
    error_message: str = ""
    raw_response_preview: str = ""


@dataclass(frozen=True)
class NativeCompletionRecord:
    schema_version: str
    inference_owner: str
    breadboard_native_lane_used: bool
    request_id: str
    upstream_request_id: str
    response_id: str
    model_ref: str
    prompt_sha256: str
    sampling_config_sha256: str
    request_sha256: str
    response_sha256: str
    output_text_sha256: str
    posthoc_token_ids_sha256: str
    backend_token_texts_sha256: str
    backend_token_logprobs_sha256: str
    backend_token_ids_sha256: str
    posthoc_token_count: int
    backend_token_text_count: int
    backend_token_logprob_count: int
    backend_token_id_count: int
    latency_ms: float
    http_status: int
    backend_completion_id: str
    passed: bool
    error_type: str = ""
    error_message: str = ""
    recorded_at_unix_ms: int = field(default_factory=lambda: int(time.time() * 1000))


class NativeInferenceLane:
    """BreadBoard-owned request/response adapter for target inference evidence.

    The model engine can still be vLLM. This class owns the sub-inference request
    path: request id, payload construction, response id, response normalization,
    token/text hashes, latency, and append-only evidence records.
    """

    def __init__(
        self,
        *,
        model_ref: str,
        base_url: str,
        tokenizer: TokenizerLike,
        request_log_path: Path,
        target_run_id: str,
        session: CompletionSession | None = None,
        timeout_seconds: float = 180.0,
    ) -> None:
        if not model_ref:
            raise ValueError("model_ref is required")
        if not base_url:
            raise ValueError("base_url is required")
        self.model_ref = model_ref
        self.base_url = base_url.rstrip("/")
        self.tokenizer = tokenizer
        self.request_log_path = request_log_path
        self.target_run_id = target_run_id
        self.timeout_seconds = timeout_seconds
        if session is None:
            import requests

            session = requests
        self.session = session
        self.generate_calls = 0
        self.last_response: NativeCompletionResponse | None = None
        self.request_log_path.parent.mkdir(parents=True, exist_ok=True)

    def generate_completion(
        self,
        *,
        upstream_request_id: object,
        prompt_ids: list[int],
        sampling_params: dict[str, Any],
    ) -> NativeCompletionResponse:
        self.generate_calls += 1
        upstream_id = _safe_id_part(upstream_request_id)
        request_id = f"bbreq-{_safe_id_part(self.target_run_id)}-{upstream_id}-{uuid.uuid4().hex[:12]}"
        prompt_text = self.tokenizer.decode(list(prompt_ids), skip_special_tokens=False)
        max_tokens = int(sampling_params.get("max_tokens", 128))
        temperature = float(sampling_params.get("temperature", 0.0))
        payload = {
            "model": self.model_ref,
            "prompt": prompt_text,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "logprobs": int(sampling_params.get("logprobs", 1)),
        }
        started = time.perf_counter()
        http_status = 0
        response_payload: dict[str, Any] = {}
        output_text = ""
        posthoc_token_ids: list[int] = []
        backend_token_texts: list[str] = []
        backend_token_logprobs: list[float] = []
        backend_token_ids: list[int] = []
        backend_completion_id = ""
        error_type = ""
        error_message = ""
        passed = False
        try:
            response = self.session.post(f"{self.base_url}/v1/completions", json=payload, timeout=self.timeout_seconds)
            http_status = int(getattr(response, "status_code", 0) or 0)
            response.raise_for_status()
            response_payload = response.json()
            choices = response_payload.get("choices") or []
            choice = choices[0] if choices else {}
            output_text = str(choice.get("text") or "")
            logprobs = choice.get("logprobs") if isinstance(choice, dict) else None
            if isinstance(logprobs, dict):
                backend_token_texts = [str(token) for token in (logprobs.get("tokens") or [])]
                backend_token_logprobs = [float(value) for value in (logprobs.get("token_logprobs") or []) if value is not None]
            raw_backend_token_ids = choice.get("token_ids") if isinstance(choice, dict) else None
            if isinstance(raw_backend_token_ids, list):
                backend_token_ids = [int(token_id) for token_id in raw_backend_token_ids]
            posthoc_token_ids = self.tokenizer.encode(output_text, add_special_tokens=False)
            backend_completion_id = str(response_payload.get("id") or "")
            passed = True
        except Exception as exc:  # noqa: BLE001
            error_type = exc.__class__.__name__
            error_message = str(exc)
            response_payload = {"error_type": error_type, "error_message": error_message}
        latency_ms = (time.perf_counter() - started) * 1000.0
        response_id = "bbresp-" + hashlib.sha256(
            f"{request_id}:{backend_completion_id}:{sha256_json(response_payload)}".encode("utf-8")
        ).hexdigest()[:24]
        record = NativeCompletionRecord(
            schema_version=BREADBOARD_NATIVE_LANE_SCHEMA,
            inference_owner=BREADBOARD_NATIVE_INFERENCE_OWNER,
            breadboard_native_lane_used=True,
            request_id=request_id,
            upstream_request_id=str(upstream_request_id),
            response_id=response_id,
            model_ref=self.model_ref,
            prompt_sha256=sha256_text(prompt_text),
            sampling_config_sha256=sha256_json({"max_tokens": max_tokens, "temperature": temperature, "logprobs": payload["logprobs"]}),
            request_sha256=sha256_json(payload),
            response_sha256=sha256_json(response_payload),
            output_text_sha256=sha256_text(output_text),
            posthoc_token_ids_sha256=sha256_json(posthoc_token_ids),
            backend_token_texts_sha256=sha256_json(backend_token_texts),
            backend_token_logprobs_sha256=sha256_json(backend_token_logprobs),
            backend_token_ids_sha256=sha256_json(backend_token_ids),
            posthoc_token_count=len(posthoc_token_ids),
            backend_token_text_count=len(backend_token_texts),
            backend_token_logprob_count=len(backend_token_logprobs),
            backend_token_id_count=len(backend_token_ids),
            latency_ms=latency_ms,
            http_status=http_status,
            backend_completion_id=backend_completion_id,
            passed=passed,
            error_type=error_type,
            error_message=error_message,
        )
        self._append_record(record)
        native_response = NativeCompletionResponse(
            request_id=request_id,
            upstream_request_id=str(upstream_request_id),
            response_id=response_id,
            model_ref=self.model_ref,
            prompt_text=prompt_text,
            output_text=output_text,
            posthoc_token_ids=posthoc_token_ids,
            backend_token_texts=backend_token_texts,
            backend_token_logprobs=backend_token_logprobs,
            backend_token_ids=backend_token_ids,
            latency_ms=latency_ms,
            http_status=http_status,
            request_sha256=record.request_sha256,
            response_sha256=record.response_sha256,
            output_text_sha256=record.output_text_sha256,
            posthoc_token_ids_sha256=record.posthoc_token_ids_sha256,
            backend_token_texts_sha256=record.backend_token_texts_sha256,
            backend_token_logprobs_sha256=record.backend_token_logprobs_sha256,
            backend_token_ids_sha256=record.backend_token_ids_sha256,
            backend_completion_id=backend_completion_id,
            passed=passed,
            error_type=error_type,
            error_message=error_message,
            raw_response_preview=json.dumps(response_payload, sort_keys=True)[:1000],
        )
        self.last_response = native_response
        if not passed:
            raise RuntimeError(f"native inference request failed: {error_type}: {error_message}")
        return native_response

    def status(self) -> dict[str, Any]:
        last = self.last_response
        return {
            "schema_version": BREADBOARD_NATIVE_LANE_SCHEMA,
            "inference_owner": BREADBOARD_NATIVE_INFERENCE_OWNER,
            "breadboard_native_lane_used": True,
            "generate_calls": self.generate_calls,
            "request_log_path": str(self.request_log_path),
            "request_log_sha256": sha256_file(self.request_log_path) if self.request_log_path.exists() else "",
            "last_request_id": last.request_id if last else "",
            "last_response_id": last.response_id if last else "",
            "last_model_ref": last.model_ref if last else self.model_ref,
            "last_output_text": last.output_text[:1000] if last else "",
            "last_output_text_sha256": last.output_text_sha256 if last else "",
            "last_posthoc_token_ids_sha256": last.posthoc_token_ids_sha256 if last else "",
            "last_backend_token_texts_sha256": last.backend_token_texts_sha256 if last else "",
            "last_backend_token_logprobs_sha256": last.backend_token_logprobs_sha256 if last else "",
            "last_backend_token_ids_sha256": last.backend_token_ids_sha256 if last else "",
            "last_posthoc_token_count": len(last.posthoc_token_ids) if last else 0,
            "last_backend_token_text_count": len(last.backend_token_texts) if last else 0,
            "last_backend_token_logprob_count": len(last.backend_token_logprobs) if last else 0,
            "last_backend_token_id_count": len(last.backend_token_ids) if last else 0,
            "last_http_status": last.http_status if last else 0,
            "last_latency_ms": last.latency_ms if last else 0.0,
        }

    def _append_record(self, record: NativeCompletionRecord) -> None:
        with self.request_log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(asdict(record), sort_keys=True, separators=(",", ":")) + "\n")
