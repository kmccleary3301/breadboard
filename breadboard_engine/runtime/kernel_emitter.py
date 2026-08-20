from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Protocol
import json

from ..compilation.primitive_records import PrimitiveCompileError, canonical_record_bytes, finalize_record, get_spec, sha256_ref

KernelEmissionMode = Literal["strict", "quarantine", "off"]


class KernelEmitter(Protocol):
    def emit(self, schema_version: str, record: Mapping[str, Any]) -> Mapping[str, Any]:
        """Finalize, validate, and persist a kernel runtime record."""

    def flush(self) -> None: ...

    def close(self) -> None: ...


def primitive_emission_enabled() -> bool:
    import os

    return os.environ.get("BREADBOARD_EMIT_PRIMITIVES", "").strip().lower() in {"1", "true", "yes", "on"}


def primitive_emission_mode(default: KernelEmissionMode = "strict") -> KernelEmissionMode:
    import os

    raw = os.environ.get("BREADBOARD_PRIMITIVES_MODE", default).strip().lower()
    if raw in {"strict", "quarantine", "off"}:
        return raw  # type: ignore[return-value]
    return default


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_line(value: Mapping[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def _transcript_content_digest(record: Mapping[str, Any]) -> str:
    contents = []
    items = record.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, Mapping):
                continue
            visibility = item.get("visibility")
            if isinstance(visibility, Mapping) and visibility.get("model_visible") is False:
                continue
            contents.append(item.get("content"))
    return sha256_ref(canonical_record_bytes(contents))


class JsonlKernelEmitter:
    def __init__(
        self,
        run_dir: Path,
        *,
        mode: KernelEmissionMode,
        clock: Callable[[], str] | None = None,
        require_payload_schema: bool = False,
        require_tool_spec_ref: bool = False,
    ) -> None:
        self.run_dir = Path(run_dir)
        self.records_dir = self.run_dir / "records"
        self.mode = mode
        self._clock = clock or _utc_now
        self.require_payload_schema = require_payload_schema
        self.require_tool_spec_ref = require_tool_spec_ref
        self._handles: dict[str, Any] = {}
        self._closed = False
        self.counts_by_schema: dict[str, int] = {}
        self.quarantine_count = 0
        self.session_id: str | None = None
        self.run_id: str | None = None
        self.session_transcript_digest: str | None = None
        self.started_at_utc = self._clock()
        if self.mode != "off":
            self.records_dir.mkdir(parents=True, exist_ok=True)
            self._write_manifest()

    def emit(self, schema_version: str, record: Mapping[str, Any]) -> Mapping[str, Any]:
        if self._closed:
            raise RuntimeError("JsonlKernelEmitter is closed")
        source = dict(record)
        if self.mode == "off":
            return source
        try:
            finalized = finalize_record(get_spec(schema_version), source, validate=True)
            self._enforce_policy(schema_version, finalized)
        except PrimitiveCompileError as exc:
            if self.mode == "quarantine":
                self._quarantine(schema_version, source, exc)
                return source
            raise
        self._remember_ids(finalized)
        if schema_version == "bb.session_transcript.v2":
            self.session_transcript_digest = _transcript_content_digest(finalized)
        self._write_record(schema_version, finalized)
        self.counts_by_schema[schema_version] = self.counts_by_schema.get(schema_version, 0) + 1
        self._write_manifest()
        return finalized

    def flush(self) -> None:
        for handle in self._handles.values():
            handle.flush()
        if self.mode != "off":
            self._write_manifest()

    def close(self) -> None:
        if self._closed:
            return
        self.flush()
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()
        self._closed = True

    def _enforce_policy(self, schema_version: str, record: Mapping[str, Any]) -> None:
        errors: list[tuple[str, str]] = []
        if schema_version == "bb.kernel_event.v2" and self.require_payload_schema:
            value = record.get("payload_schema_version")
            if not isinstance(value, str) or not value:
                errors.append(("/payload_schema_version", "payload_schema_version is required by emitter policy"))
        if schema_version == "bb.tool_call.v2" and self.require_tool_spec_ref:
            value = record.get("tool_spec_ref")
            if not isinstance(value, str) or not value:
                errors.append(("/tool_spec_ref", "tool_spec_ref is required by emitter policy"))
        if errors:
            raise PrimitiveCompileError(
                schema_version=schema_version,
                record_id=str(record.get("event_id") or record.get("call_id") or "") or None,
                errors=errors,
                hint="runtime emission payload discipline requires explicit schema/spec references",
            )

    def _quarantine(self, schema_version: str, source: Mapping[str, Any], exc: PrimitiveCompileError) -> None:
        envelope = {
            "schema_version": schema_version,
            "error": str(exc),
            "errors": [{"pointer": pointer, "message": message} for pointer, message in exc.errors],
            "record": dict(source),
            "quarantined_at_utc": self._clock(),
        }
        self._write_record("_quarantine", envelope)
        self.quarantine_count += 1
        self._write_manifest()

    def _remember_ids(self, record: Mapping[str, Any]) -> None:
        session_id = record.get("session_id")
        run_id = record.get("run_id")
        if isinstance(session_id, str) and session_id:
            self.session_id = session_id
        if isinstance(run_id, str) and run_id:
            self.run_id = run_id

    def _write_record(self, stream: str, record: Mapping[str, Any]) -> None:
        handle = self._handle(stream)
        handle.write(_json_line(record))

    def _handle(self, stream: str) -> Any:
        handle = self._handles.get(stream)
        if handle is None:
            path = self.records_dir / f"{stream}.jsonl"
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("a", encoding="utf-8")
            self._handles[stream] = handle
        return handle

    def _write_manifest(self) -> None:
        manifest = {
            "session_id": self.session_id,
            "run_id": self.run_id,
            "started_at_utc": self.started_at_utc,
            "counts_by_schema": dict(sorted(self.counts_by_schema.items())),
            "quarantine_count": self.quarantine_count,
            "session_transcript_digest": self.session_transcript_digest,
        }
        tmp_path = self.run_dir / "manifest.json.tmp"
        tmp_path.write_text(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        tmp_path.replace(self.run_dir / "manifest.json")


__all__ = [
    "JsonlKernelEmitter",
    "KernelEmitter",
    "KernelEmissionMode",
    "primitive_emission_enabled",
    "primitive_emission_mode",
]
