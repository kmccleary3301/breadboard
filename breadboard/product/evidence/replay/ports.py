from __future__ import annotations

import json
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from .plan import ReplayPlan, canonical_json

def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("replay transcript keys must be strings")
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    return value


class ReplayWorker(Protocol):
    worker_id: str

    def execute(self, plan: ReplayPlan, input_bytes: bytes) -> "ReplayWorkerResult": ...


class ReplayWorkerIntegrityError(RuntimeError):
    pass

class ReplayWorkerCanceled(RuntimeError):
    pass


class ReplayWorkerTimedOut(RuntimeError):
    pass


class ReplayWorkerProcessError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ReplayWorkerResult:
    outputs: Mapping[str, bytes]
    transcript: tuple[Mapping[str, Any], ...]

    def __post_init__(self) -> None:
        outputs = dict(self.outputs)
        if any(not isinstance(path, str) or not isinstance(content, bytes) for path, content in outputs.items()):
            raise TypeError("replay worker outputs must map paths to bytes")
        if any(not isinstance(row, Mapping) for row in self.transcript):
            raise TypeError("replay transcript rows must be mappings")
        transcript = tuple(_freeze_json(row) for row in self.transcript)
        object.__setattr__(self, "outputs", MappingProxyType(outputs))
        object.__setattr__(self, "transcript", transcript)

    def transcript_bytes(self) -> bytes:
        return canonical_json({"schema_version": "bb.replay_transcript.v1", "events": list(self.transcript)}) + b"\n"


class TapeReplayWorker:
    """Deterministic worker for a canonical JSON replay tape."""

    worker_id = "tape/json-v1"

    def execute(self, plan: ReplayPlan, input_bytes: bytes) -> ReplayWorkerResult:
        if plan.worker_id != self.worker_id:
            raise ValueError("replay plan worker_id does not select this worker")
        try:
            tape = json.loads(input_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise ValueError("replay tape must be canonical JSON") from error
        if not isinstance(tape, dict) or tape.get("schema_version") != "bb.replay_tape.v1":
            raise ValueError("unsupported replay tape schema_version")
        raw_steps, raw_outputs = tape.get("steps"), tape.get("outputs")
        if not isinstance(raw_steps, list) or not isinstance(raw_outputs, dict):
            raise ValueError("replay tape requires steps and outputs")
        transcript: list[Mapping[str, Any]] = []
        known_spans: set[str] = set()
        for sequence, raw in enumerate(raw_steps, 1):
            if not isinstance(raw, dict):
                raise ValueError("replay tape steps must be objects")
            kind, span_id, parent = raw.get("kind"), raw.get("span_id"), raw.get("parent_span_id")
            if not isinstance(kind, str) or not kind or not isinstance(span_id, str) or not span_id:
                raise ReplayWorkerIntegrityError("replay transcript step identity is invalid")
            if parent is not None and (not isinstance(parent, str) or not parent):
                raise ReplayWorkerIntegrityError("replay transcript parent span is invalid")
            if span_id in known_spans or parent is not None and parent not in known_spans:
                raise ReplayWorkerIntegrityError("replay transcript causal spans are invalid")
            payload = raw.get("payload", {})
            if not isinstance(payload, dict):
                raise ReplayWorkerIntegrityError("replay transcript payload must be an object")
            transcript.append(
                {
                    "sequence": sequence,
                    "kind": kind,
                    "span_id": span_id,
                    "parent_span_id": parent,
                    "payload": json.loads(canonical_json(payload)),
                }
            )
            known_spans.add(span_id)
        if any(not isinstance(path, str) for path in raw_outputs):
            raise ReplayWorkerIntegrityError("replay tape output paths must be strings")
        outputs = {path: canonical_json(value) + b"\n" for path, value in raw_outputs.items()}
        if plan.transcript_path in outputs:
            raise ReplayWorkerIntegrityError("replay tape cannot replace its normalized transcript")
        return ReplayWorkerResult(outputs, tuple(transcript))
