from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from breadboard.rl.phase4.native_inference import (
    BREADBOARD_NATIVE_INFERENCE_OWNER,
    BREADBOARD_NATIVE_LANE_SCHEMA,
    NativeInferenceLane,
)


def _sha256_json(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


class FakeTokenizer:
    def __init__(self) -> None:
        self.decode_calls: list[tuple[list[int], bool]] = []
        self.encode_calls: list[tuple[str, bool]] = []

    def decode(self, token_ids: list[int], *, skip_special_tokens: bool = False) -> str:
        self.decode_calls.append((list(token_ids), skip_special_tokens))
        return "prompt<" + ",".join(str(token_id) for token_id in token_ids) + ">"

    def encode(self, text: str, *, add_special_tokens: bool = False) -> list[int]:
        self.encode_calls.append((text, add_special_tokens))
        if text == "alpha beta":
            return [101, 202, 303]
        return [len(text)]


class FakeCompletionResponse:
    def __init__(self, *, payload: dict[str, Any], status_code: int = 200, failure: Exception | None = None) -> None:
        self._payload = payload
        self.status_code = status_code
        self._failure = failure

    def raise_for_status(self) -> None:
        if self._failure is not None:
            raise self._failure

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeCompletionSession:
    def __init__(self, response: FakeCompletionResponse | None = None, failure: Exception | None = None) -> None:
        self.response = response
        self.failure = failure
        self.posts: list[tuple[str, dict[str, Any], float]] = []

    def post(self, url: str, *, json: dict[str, Any], timeout: float) -> FakeCompletionResponse:
        self.posts.append((url, dict(json), timeout))
        if self.failure is not None:
            raise self.failure
        assert self.response is not None
        return self.response


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_native_lane_records_breadboard_ids_and_separate_backend_token_evidence(tmp_path: Path) -> None:
    log_path = tmp_path / "native" / "requests.jsonl"
    response_payload = {
        "id": "cmpl-backend-42",
        "choices": [
            {
                "text": "alpha beta",
                "token_ids": [9101, 9102],
                "logprobs": {
                    "tokens": ["alpha", " beta"],
                    "token_logprobs": [-0.125, -0.5],
                },
            }
        ],
    }
    session = FakeCompletionSession(response=FakeCompletionResponse(payload=response_payload))
    tokenizer = FakeTokenizer()
    lane = NativeInferenceLane(
        model_ref="fake/native-model",
        base_url="http://127.0.0.1:8000/",
        tokenizer=tokenizer,
        request_log_path=log_path,
        target_run_id="target run/01",
        session=session,
        timeout_seconds=7.5,
    )

    native_response = lane.generate_completion(
        upstream_request_id="rollout request/7",
        prompt_ids=[11, 22],
        sampling_params={"max_tokens": 3, "temperature": 0.25, "logprobs": 2},
    )

    assert session.posts == [
        (
            "http://127.0.0.1:8000/v1/completions",
            {
                "model": "fake/native-model",
                "prompt": "prompt<11,22>",
                "max_tokens": 3,
                "temperature": 0.25,
                "logprobs": 2,
            },
            7.5,
        )
    ]
    assert tokenizer.decode_calls == [([11, 22], False)]
    assert tokenizer.encode_calls == [("alpha beta", False)]
    assert native_response.request_id.startswith("bbreq-target-run-01-rollout-request-7-")
    assert native_response.response_id.startswith("bbresp-")
    assert native_response.upstream_request_id == "rollout request/7"
    assert native_response.passed is True
    assert native_response.backend_completion_id == "cmpl-backend-42"
    assert native_response.backend_token_texts == ["alpha", " beta"]
    assert native_response.backend_token_logprobs == [-0.125, -0.5]
    assert native_response.backend_token_ids == [9101, 9102]
    assert native_response.posthoc_token_ids == [101, 202, 303]

    records = _read_jsonl(log_path)
    assert len(records) == 1
    record = records[0]
    assert record["schema_version"] == BREADBOARD_NATIVE_LANE_SCHEMA
    assert record["inference_owner"] == BREADBOARD_NATIVE_INFERENCE_OWNER
    assert record["breadboard_native_lane_used"] is True
    assert record["request_id"] == native_response.request_id
    assert record["response_id"] == native_response.response_id
    assert record["passed"] is True
    assert record["http_status"] == 200
    assert record["posthoc_token_count"] == 3
    assert record["backend_token_text_count"] == 2
    assert record["backend_token_id_count"] == 2
    assert record["backend_token_logprob_count"] == 2
    assert record["backend_token_texts_sha256"] == _sha256_json(["alpha", " beta"])
    assert record["backend_token_ids_sha256"] == _sha256_json([9101, 9102])
    assert record["backend_token_logprobs_sha256"] == _sha256_json([-0.125, -0.5])
    assert record["posthoc_token_ids_sha256"] == _sha256_json([101, 202, 303])
    assert record["backend_token_texts_sha256"] != record["posthoc_token_ids_sha256"]
    assert record["backend_token_logprobs_sha256"] != record["posthoc_token_ids_sha256"]

    status = lane.status()
    assert status["schema_version"] == BREADBOARD_NATIVE_LANE_SCHEMA
    assert status["inference_owner"] == BREADBOARD_NATIVE_INFERENCE_OWNER
    assert status["generate_calls"] == 1
    assert status["last_request_id"] == native_response.request_id
    assert status["last_response_id"] == native_response.response_id
    assert status["last_backend_token_text_count"] == 2
    assert status["last_backend_token_id_count"] == 2
    assert status["last_backend_token_logprob_count"] == 2
    assert status["request_log_sha256"] == _sha256_file(log_path)


def test_native_lane_appends_failed_evidence_row_before_raising(tmp_path: Path) -> None:
    log_path = tmp_path / "requests.jsonl"
    session = FakeCompletionSession(failure=ConnectionError("target session refused connection"))
    lane = NativeInferenceLane(
        model_ref="fake/native-model",
        base_url="http://127.0.0.1:8000",
        tokenizer=FakeTokenizer(),
        request_log_path=log_path,
        target_run_id="target-run-02",
        session=session,
        timeout_seconds=1.25,
    )

    with pytest.raises(RuntimeError, match="native inference request failed: ConnectionError: target session refused connection"):
        lane.generate_completion(
            upstream_request_id="rollout-99",
            prompt_ids=[5],
            sampling_params={"max_tokens": 1, "temperature": 0.0},
        )

    assert session.posts == [
        (
            "http://127.0.0.1:8000/v1/completions",
            {
                "model": "fake/native-model",
                "prompt": "prompt<5>",
                "max_tokens": 1,
                "temperature": 0.0,
                "logprobs": 1,
            },
            1.25,
        )
    ]
    records = _read_jsonl(log_path)
    assert len(records) == 1
    record = records[0]
    assert record["inference_owner"] == BREADBOARD_NATIVE_INFERENCE_OWNER
    assert record["request_id"].startswith("bbreq-target-run-02-rollout-99-")
    assert record["response_id"].startswith("bbresp-")
    assert record["passed"] is False
    assert record["http_status"] == 0
    assert record["error_type"] == "ConnectionError"
    assert record["error_message"] == "target session refused connection"
    assert record["posthoc_token_count"] == 0
    assert record["backend_token_text_count"] == 0
    assert record["backend_token_id_count"] == 0
    assert record["backend_token_logprob_count"] == 0

    status = lane.status()
    assert status["generate_calls"] == 1
    assert status["last_request_id"] == record["request_id"]
    assert status["last_response_id"] == record["response_id"]
    assert status["last_http_status"] == 0
    assert status["request_log_sha256"] == _sha256_file(log_path)
